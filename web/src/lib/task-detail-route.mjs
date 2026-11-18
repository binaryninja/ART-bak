export function taskDetailHref(task) {
  const page = task.runtime_kind === "servicenow-recon" ? "/function/recon" : "/function/tasks/detail";
  return `${page}?id=${encodeURIComponent(String(task.id))}`;
}
