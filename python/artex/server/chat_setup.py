"""Opt-in conversation runtime assembly; no registration, listener or default tools.

Requires an explicit profile runtime (and therefore host-approved transport),
existing transcript root, authorization callback, grant gate, tools and agent
system-prompt resolver. The host registers service routes on its authenticated
Router. Authorization is checked again on actual model/tool admission. Model
identity is an opaque per-bundle HMAC of the full private configuration, including
keys; changed configuration fails rather than silently switching a queued run.
Only nonstreaming profile execution is supported by the current ChatAgent.

Dispatcher owns explicit bounded worker threads. Call close(cancel=True) and
check DrainResult before closing DB/transports or deleting transcripts. A timed
out drain does NOT mean execution stopped. No callback can forcibly interrupt a
blocking transport/tool. ProfileRuntime old-provider draining also remains host
owned. No cross-process admission, crash recovery, provider probing or automatic
replay is supplied. User activity, JSONL admission/finalization, and terminal DB
activity are separate durable steps, NOT a distributed transaction. Failure may
leave accepted user activity or finalized JSONL without terminal activity; do not
blindly retry. Dispatcher receipts and transcript client IDs prevent local rerun.
Activities project user and terminal result only, not incremental tool progress.
"""
from copy import deepcopy
from dataclasses import asdict, dataclass, field
import hashlib
import hmac
import json
import os
from ..agent.chat import ChatBinding, ChatUnavailable
from ..agent.runtime import Limits
from ..chat_dispatch import ChatDispatcher
from ..chat_transcripts import ChatTranscriptAdapter
from ..transcripts import TranscriptStore
from ..db.conversations import ConversationStore, Conversation
from ..db.conversation_activity import ConversationActivityStore
from ..sidequestion import Model
from .runtime_setup import RuntimeBundle
from .chat import ChatResources, ChatService


@dataclass(frozen=True)
class ChatRuntimeBundle:
    service: ChatService = field(repr=False)
    dispatcher: ChatDispatcher = field(repr=False)
    conversations: ConversationStore = field(repr=False)
    transcript: ChatTranscriptAdapter = field(repr=False)
    activities: object = field(repr=False)

    def close(self, *, cancel=False, timeout=None):
        return self.dispatcher.close(cancel=cancel, timeout=timeout)


def build_conversation_runtime(executor, profile_runtime, *, transcript_root,
                               authorize, gate, tools, system_prompt, single_process,
                               workers=1, capacity=4, max_receipts=10000,
                               capture=None, limits=Limits()):
    """Return an unregistered service and explicit owned dispatcher.

    authorize(request, conversation)->True is required for every live resource
    selection and boundary; system_prompt(conversation)->str must be pure.
    tools is an explicit iterable (use () for none), never a default registry.
    profile_runtime must use the same authorized database deployment as executor.
    Construction reads no conversation or private profile and calls no provider.
    """
    if single_process is not True:
        raise ValueError('Explicit single-process conversation authority required')
    if not isinstance(profile_runtime, RuntimeBundle):
        raise ValueError('Explicit profile RuntimeBundle required')
    if profile_runtime.profile_store.executor is not executor:
        raise ValueError('Profile and conversation database authority must match')
    if not all(callable(x) for x in (authorize, gate, system_prompt)):
        raise ValueError('Explicit authorization, grant gate and system resolver required')
    if not isinstance(limits, Limits) or (capture is not None and not callable(capture)):
        raise ValueError('Invalid chat runtime configuration')
    tools = tuple(tools)
    # Pure registry validation before creating threads or opening transcript root.
    from ..agent.runtime import BoundedAgent
    class NoProvider:
        def complete(self, *_):
            raise ChatUnavailable('No default provider')
    BoundedAgent(NoProvider(), gate=gate, tools=tools, limits=limits)
    transcript = ChatTranscriptAdapter(TranscriptStore(transcript_root))
    conversations = ConversationStore(executor)
    salt = os.urandom(32)  # local opaque identity, not an account credential

    def activities(identifier):
        return ConversationActivityStore(executor, identifier)

    def model_for(conversation):
        store = profile_runtime.profile_store
        profile = store.get(conversation.llm_profile_id) if conversation.llm_profile_id is not None else store.active()
        if profile is None or profile.streaming:
            raise ChatUnavailable('An explicit nonstreaming profile is required')
        if conversation.llm_profile_id is None and not profile_runtime.profile_coordinator.snapshot()['configured']:
            raise ChatUnavailable('Active profile runtime unavailable')
        profile_runtime.profile_factory.validate(profile)
        raw = json.dumps(asdict(profile), sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
        identity = hmac.new(salt, raw, hashlib.sha256).hexdigest()
        model = Model(profile_id=profile.id, name=profile.name, format=profile.format,
                      model=profile.model, identity=identity, streaming=False,
                      window_tokens=max(32, min(1000, profile.context_window_k or 200)) * 1000)
        return model, profile

    def resolve(request, conversation):
        if not isinstance(conversation, Conversation) or authorize(request, deepcopy(conversation)) is not True:
            raise ChatUnavailable('Conversation authority unavailable')
        if conversation.agent_key == 'retester':
            raise ChatUnavailable('Finding retest lifecycle is not implemented')
        model, _ = model_for(conversation)
        prompt = system_prompt(deepcopy(conversation))
        if not isinstance(prompt, str):
            raise ChatUnavailable('Agent system prompt unavailable')

        def live():
            current = conversations.get(conversation.id)
            if (not isinstance(current, Conversation) or current.id != conversation.id
                or (current.agent_key, current.llm_profile_id) != (conversation.agent_key, conversation.llm_profile_id)
                or authorize(request, deepcopy(current)) is not True):
                raise ChatUnavailable('Conversation authority changed')
            return current

        def binding(expected):
            actual, profile = model_for(live())
            if actual != expected:
                raise ChatUnavailable('Selected profile changed')
            provider = profile_runtime.profile_factory.build(profile)
            return ChatBinding(actual, provider)

        def checked_gate(context, action):
            live()
            grant = gate(context, action)
            live()
            return grant

        return ChatResources(transcript=transcript, model=model, resolver=binding,
            gate=checked_gate, durable_result=transcript.finalized_result, tools=tools,
            system=prompt, capture=capture, limits=limits)

    dispatcher = ChatDispatcher(workers=workers, capacity=capacity, max_receipts=max_receipts, single_process=True)
    try:
        service = ChatService(conversations, resolve, dispatcher=dispatcher, activities=activities, authorize_conversation=authorize)
    except BaseException:
        dispatcher.close(cancel=True)
        raise
    return ChatRuntimeBundle(service, dispatcher, conversations, transcript, activities)


@dataclass(frozen=True)
class ChatApplicationBundle:
    """One authenticated WSGI app plus explicit runtime ownership/drain handle."""
    application: object = field(repr=False)
    profiles: RuntimeBundle = field(repr=False)
    chat: ChatRuntimeBundle = field(repr=False)

    def close(self, *, cancel=False, timeout=None):
        return self.chat.close(cancel=cancel, timeout=timeout)


def build_chat_application(executor, signing_key, password_codec, *,
                           transport_factory, health, transcript_root,
                           authorize, gate, tools, agent_available, system_prompt,
                           single_process, workers=1, capacity=4, max_receipts=10000,
                           capture=None, limits=Limits(), application_options=None):
    """Opt-in complete configuration/conversation WSGI wiring, without listening.

    agent_available(key)->literal True defines the host's supported agent set;
    unknown keys and source 'retester' cannot be newly created or executed.
    Metadata lists retain existing records so users can still inspect history.
    All routes share the profile application's JWT authentication. Profile and
    task-state startup reads are explicit; no inference or provider call occurs.
    Metadata callbacks are late-bound but construction never invokes them.
    Main-task POST /api/chat remains 503; this is conversation execution only.
    On assembly failure any newly created dispatcher is cancelled and drained.
    """
    from .runtime_setup import build_profile_runtime_application
    if not callable(agent_available) or not callable(system_prompt):
        raise ValueError('Explicit agent registry and prompt resolver required')
    if single_process is not True or not callable(authorize) or not callable(gate):
        raise ValueError('Explicit conversation authority and gate required')
    options = dict(application_options or {})
    reserved = {'conversation_running', 'conversation_agent_available', 'conversation_profile_available'}
    if reserved & options.keys():
        raise ValueError('Conversation adapters are owned by this assembly')
    state = {}

    def supported(key):
        return key != 'retester' and agent_available(key) is True

    def running(identifier):
        if 'chat' not in state:
            raise ChatUnavailable('Conversation dispatcher not initialized')
        return state['chat'].dispatcher.running(identifier)

    def available_profile(identifier):
        if 'profiles' not in state:
            raise ChatUnavailable('Profile runtime not initialized')
        profiles = state['profiles']
        profile = profiles.profile_store.get(identifier)
        if profile is None or profile.streaming:
            return False
        try:
            profiles.profile_factory.validate(profile)
        except ValueError:
            return False
        return True

    def prompt(conversation):
        if not supported(conversation.agent_key):
            raise ChatUnavailable('Agent runtime unavailable')
        return system_prompt(conversation)

    profiles = build_profile_runtime_application(executor, signing_key, password_codec,
        transport_factory=transport_factory, health=health,
        application_options=dict(options, conversation_running=running,
            conversation_agent_available=supported, conversation_profile_available=available_profile))
    state['profiles'] = profiles
    chat = None
    try:
        chat = build_conversation_runtime(executor, profiles,
            transcript_root=transcript_root, authorize=authorize, gate=gate, tools=tools,
            system_prompt=prompt, single_process=single_process, workers=workers,
            capacity=capacity, max_receipts=max_receipts, capture=capture, limits=limits)
        state['chat'] = chat
        chat.service.register(profiles.application.router)
        return ChatApplicationBundle(profiles.application, profiles, chat)
    except BaseException:
        if chat is not None:
            chat.close(cancel=True)
        raise
