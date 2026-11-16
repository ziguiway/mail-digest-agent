import { createDatabaseClient, createSiteContext, errorResponse } from './_qoder/backend.mjs';
import { application, publicError } from './routes.mjs';

const handle = application({ db: createDatabaseClient() });
Deno.serve(async (request) => {
  let context;
  try {
    context = createSiteContext(request);
    return await handle(context);
  } catch (error) {
    if (context) return publicError(error);
    return errorResponse(error, context?.requestId);
  }
});
