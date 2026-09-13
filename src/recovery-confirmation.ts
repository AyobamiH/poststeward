import { requireValue } from "./common.ts";

export type RecoveryConfirmationAction = "RESTORE" | "UNDO" | "RESUME";

export function demandRecoveryConfirmation(
  action: RecoveryConfirmationAction,
  workspace: string,
  value: unknown,
) {
  requireValue(
    value === `${action} ${workspace}`,
    "RECOVERY_CONFIRMATION_REQUIRED",
    `Type ${action} followed by the exact workspace ID before this recovery action.`,
    409,
  );
}
