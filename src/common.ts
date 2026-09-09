export class Fault extends Error {
  constructor(
    public code: string,
    message: string,
    public status = 400,
    public details?: unknown,
  ) {
    super(message);
  }
}
export function requireValue(
  condition: unknown,
  code: string,
  message: string,
  status = 400,
): asserts condition {
  if (!condition) throw new Fault(code, message, status);
}
export const uid = () => crypto.randomUUID();
export function canonical(value: unknown): string {
  if (Array.isArray(value)) return "[" + value.map(canonical).join(",") + "]";
  if (value && typeof value === "object")
    return (
      "{" +
      Object.keys(value)
        .sort()
        .map(
          (k) =>
            JSON.stringify(k) +
            ":" +
            canonical((value as Record<string, unknown>)[k]),
        )
        .join(",") +
      "}"
    );
  return JSON.stringify(value);
}
export async function digest(value: unknown): Promise<string> {
  return Array.from(
    new Uint8Array(
      await crypto.subtle.digest(
        "SHA-256",
        new TextEncoder().encode(canonical(value)),
      ),
    ),
  )
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}
export function json(
  value: unknown,
  status = 200,
  headers: HeadersInit = {},
): Response {
  return Response.json(value, {
    status,
    headers: {
      "Cache-Control": "no-store",
      ...Object.fromEntries(new Headers(headers)),
    },
  });
}
export function errorResponse(error: unknown): Response {
  return error instanceof Fault
    ? json(
        {
          error: {
            code: error.code,
            message: error.message,
            details: error.details,
          },
        },
        error.status,
      )
    : json(
        {
          error: {
            code: "INTERNAL_ERROR",
            message:
              "The operation did not complete. Inspect its durable status before retrying.",
          },
        },
        500,
      );
}
export function addMonth(at: number): number {
  const d = new Date(at),
    day = d.getUTCDate();
  d.setUTCDate(1);
  d.setUTCMonth(d.getUTCMonth() + 1);
  const last = new Date(
    Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 0),
  ).getUTCDate();
  d.setUTCDate(Math.min(day, last));
  return d.getTime();
}
export function explicitTime(value: string): number {
  requireValue(
    /T\d{2}:\d{2}(:\d{2}(\.\d{1,3})?)?(Z|[+-]\d{2}:\d{2})$/.test(value),
    "EXPLICIT_OFFSET_REQUIRED",
    "Use an ISO timestamp with Z or an explicit UTC offset.",
  );
  const n = Date.parse(value);
  requireValue(Number.isFinite(n), "INVALID_TIME", "Invalid timestamp.");
  const date = /^(\d{4})-(\d{2})-(\d{2})T/.exec(value);
  requireValue(date, "INVALID_TIME", "Use an ISO calendar date.");
  const [year, month, day] = date.slice(1).map(Number);
  const calendar = new Date(Date.UTC(year, month - 1, day));
  requireValue(
    calendar.getUTCFullYear() === year &&
      calendar.getUTCMonth() === month - 1 &&
      calendar.getUTCDate() === day,
    "INVALID_TIME",
    "This calendar date does not exist.",
  );
  return n;
}
export function validZone(zone: string): void {
  try {
    new Intl.DateTimeFormat("en", { timeZone: zone });
  } catch {
    throw new Fault("INVALID_TIMEZONE", "Use an IANA timezone.");
  }
}
export const active = new Set(["scheduled", "executing", "waiting_container"]);
