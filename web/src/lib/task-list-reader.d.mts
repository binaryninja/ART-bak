export function createTaskListReader<T>(callbacks: {
  read: (signal: AbortSignal) => Promise<T>;
  onStart: (foreground: boolean) => void;
  onData: (value: T) => void;
  onError: (error: unknown) => void;
  onSettled: () => void;
}): {
  activate(): void;
  deactivate(): void;
  retire(): void;
  run(replace?: boolean): boolean;
};
