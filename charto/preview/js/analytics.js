/* Charto preview — product analytics and error monitoring (PostHog).
 *
 * One file for both surfaces: the chart loads it from index.html, and the
 * shell (pivot-next) loads the same file from /chart-app/js/analytics.js, so
 * a visitor is one person across the shell, the framed chart and the
 * server's events.
 *
 * The project token is not in source. The data server reads it from its
 * environment (POSTHOG_PROJECT_TOKEN) and serves it at /analytics/config; an
 * empty answer means analytics is off and nothing here loads. The token is
 * PostHog's public ingestion key — it can write events, never read them.
 *
 * Identity is the server's: a signed-in user is `charto:<id>`, the same
 * distinct id data/analytics.py captures with, so page views and chat turns
 * share one timeline. Signing out resets to a fresh anonymous visitor.
 */
"use strict";

(() => {
  if (window.__pivotAnalytics) return;           // the shell and the chart frame each run it once
  window.__pivotAnalytics = true;

  const TOKEN_KEY = "charto:auth:token";
  // The chart declares `const Auth` at the top level, which is a global but
  // not a property of window; the shell has no Auth at all.
  const CHART = typeof Auth !== "undefined";
  let identified = null;

  function load(cfg) {
    // PostHog's official loader stub (posthog.com/docs/libraries/js): queues
    // calls until array.js arrives from the project's asset host.
    !function(t,e){var o,n,p,r;e.__SV||(window.posthog=e,e._i=[],e.init=function(i,s,a){function g(t,e){var o=e.split(".");2==o.length&&(t=t[o[0]],e=o[1]),t[e]=function(){t.push([e].concat(Array.prototype.slice.call(arguments,0)))}}(p=t.createElement("script")).type="text/javascript",p.crossOrigin="anonymous",p.async=!0,p.src=s.api_host.replace(".i.posthog.com","-assets.i.posthog.com")+"/static/array.js",(r=t.getElementsByTagName("script")[0]).parentNode.insertBefore(p,r);var u=e;for(void 0!==a?u=e[a]=[]:a="posthog",u.people=u.people||[],Object.defineProperty(u,"toString",{configurable:!0,enumerable:!0,writable:!0,value:function(t){var e="posthog";return"posthog"!==a&&(e+="."+a),t||(e+=" (stub)"),e}}),Object.defineProperty(u.people,"toString",{configurable:!0,enumerable:!0,writable:!0,value:function(){return u.toString(1)+".people (stub)"}}),o="init capture register register_once register_for_session unregister unregister_for_session getFeatureFlag getFeatureFlagResult isFeatureEnabled reloadFeatureFlags updateEarlyAccessFeatureEnrollment getEarlyAccessFeatures on onFeatureFlags onSessionId getSurveys getActiveMatchingSurveys renderSurvey canRenderSurvey getNextSurveyStep identify setPersonProperties group resetGroups setPersonPropertiesForFlags resetPersonPropertiesForFlags setGroupPropertiesForFlags resetGroupPropertiesForFlags reset get_distinct_id getGroups get_session_id get_session_replay_url alias set_config startSessionRecording stopSessionRecording sessionRecordingStarted captureException loadToolbar get_property getSessionProperty createPersonProfile opt_in_capturing opt_out_capturing has_opted_in_capturing has_opted_out_capturing clear_opt_in_out_capturing debug".split(" "),n=0;n<o.length;n++)g(u,o[n]);e._i.push([i,s,a])},e.__SV=1)}(document,window.posthog||[]);
    window.posthog.init(cfg.key, {
      api_host: cfg.host,
      defaults: "2026-05-30",
      person_profiles: "identified_only",   // anonymous browsing makes no person
      capture_exceptions: true,             // front-end errors, for monitoring
    });
    window.posthog.register({ surface: CHART ? (window.top === window ? "chart" : "chart-frame") : "shell" });
  }

  function identify(user) {
    if (!window.posthog) return;
    if (user && user.id != null) {
      const id = `charto:${user.id}`;
      if (identified === id) return;
      identified = id;
      window.posthog.identify(id, { email: user.email || undefined, name: user.name || undefined });
    } else if (identified) {
      identified = null;
      window.posthog.reset();
    }
  }

  /** The chart has an Auth module that knows who is signed in; the shell
   *  holds the same session token, so it asks the server once. */
  async function whoAmI() {
    if (CHART && typeof Auth.onChange === "function") {
      Auth.onChange(identify);
      return;
    }
    let tok = null;
    try { tok = localStorage.getItem(TOKEN_KEY); } catch { /* storage blocked */ }
    if (!tok) return;
    try {
      const r = await fetch("/auth/me", { headers: { Authorization: `Bearer ${tok}` } });
      if (r.ok) identify((await r.json()).user || null);
    } catch { /* offline: stay anonymous */ }
  }

  async function start() {
    let cfg = {};
    try {
      const r = await fetch("/analytics/config", { credentials: "same-origin" });
      if (r.ok) cfg = await r.json();
    } catch { return; }
    if (!cfg || !cfg.key || !cfg.host) return;
    load(cfg);
    whoAmI();
  }

  start();
})();
