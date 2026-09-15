import handler, { Workspace } from "./canary-edge.ts";
import { presentBrowserResponse } from "./browser-presentation.ts";
import type { Env } from "./types.ts";

export { Workspace };

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext) {
    const response = await handler.fetch(request, env, ctx);
    return presentBrowserResponse(request, response, env);
  },
  async scheduled(controller: ScheduledController, env: Env, ctx: ExecutionContext) {
    return handler.scheduled(controller, env, ctx);
  },
} satisfies ExportedHandler<Env>;
