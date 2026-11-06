export class TetherError extends Error {
  readonly code: string;
  readonly status: number | undefined;
  readonly retryable: boolean;
  constructor(code: string, options: { status?: number; retryable?: boolean } = {}) {
    super(code);
    this.name = "TetherError";
    this.code = code;
    this.status = options.status;
    this.retryable = options.retryable ?? false;
  }
}
