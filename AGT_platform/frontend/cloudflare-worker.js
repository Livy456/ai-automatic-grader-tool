export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (!url.pathname.startsWith("/api/")) {
      return env.ASSETS.fetch(request);
    }

    const apiOrigin = (env.API_ORIGIN || "").trim();
    if (!apiOrigin) {
      return new Response(
        "Worker misconfigured: API_ORIGIN is not set. Configure it as a Cloudflare Worker variable or secret.",
        { status: 500, headers: { "content-type": "text/plain; charset=utf-8" } },
      );
    }

    let originUrl;
    try {
      originUrl = new URL(apiOrigin);
    } catch {
      return new Response(
        "Worker misconfigured: API_ORIGIN is not a valid absolute URL.",
        { status: 500, headers: { "content-type": "text/plain; charset=utf-8" } },
      );
    }

    const target = new URL(url.pathname + url.search, originUrl);
    const headers = new Headers(request.headers);
    headers.set("Host", originUrl.host);

    return fetch(
      new Request(target.toString(), {
        method: request.method,
        headers,
        body: request.body,
        redirect: "manual",
      }),
    );
  },
};
