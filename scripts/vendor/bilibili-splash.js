/**
 * PopCat-compatible Bilibili splash ad response.
 * Returns an empty, successful splash-list payload before the app downloads
 * the server-side creative list. This only matches the documented splash/list
 * API; it does not block Bilibili images, playback or other normal requests.
 */
(function () {
  "use strict";

  const requestUrl = (typeof $request !== "undefined" && $request.url) || "";
  if (!/\/x\/v2\/splash\/list(?:\?|$)/.test(requestUrl)) {
    $done({});
    return;
  }

  const payload = {
    code: 0,
    message: "0",
    ttl: 1,
    data: {
      max_time: 0,
      min_interval: 31536000,
      pull_interval: 31536000,
      keep_ids: null,
      list: []
    }
  };

  console.log("[BiliSplash] returned an empty splash ad list");
  $done({
    status: 200,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store"
    },
    body: JSON.stringify(payload)
  });
})();
