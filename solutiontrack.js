/*!
 * SolutionTrack Pixel  -  solutiontrack.js
 * Captura UTMs, gera/mantem _fbp e _fbc, decora links de checkout
 * e envia PageView / InitiateCheckout para o servidor.
 * Instale na pagina de vendas:  <script src="SEU_DOMINIO/solutiontrack.js"></script>
 */
(function () {
  "use strict";

  var ENDPOINT = "__SOLUTIONTRACK_ENDPOINT__"; // injetado pelo servidor
  var UTM_KEYS = ["utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term", "src", "sck"];
  var COOKIE_DAYS = 90;

  // Dominios de checkout decorados automaticamente
  var CHECKOUT_HOSTS = [
    "kiwify.com", "kiwify.com.br", "pay.kiwify",
    "hotmart.com", "pay.hotmart",
    "greenn.com.br", "greenn.com", "pay.greenn",
    "cakto.com.br", "pay.cakto",
    "wiapy.com", "pay.wiapy"
  ];

  // ---------------- COOKIES ----------------
  function setCookie(name, value, days) {
    var d = new Date();
    d.setTime(d.getTime() + days * 24 * 60 * 60 * 1000);
    document.cookie = name + "=" + encodeURIComponent(value) + ";expires=" + d.toUTCString() + ";path=/;SameSite=Lax";
  }
  function getCookie(name) {
    var m = document.cookie.match("(^|;)\\s*" + name + "\\s*=\\s*([^;]+)");
    return m ? decodeURIComponent(m.pop()) : null;
  }

  // ---------------- UTM CAPTURE ----------------
  function captureUTMs() {
    var params = new URLSearchParams(window.location.search);
    var stored = {};
    UTM_KEYS.forEach(function (k) {
      var val = params.get(k);
      if (val) {
        setCookie("st_" + k, val, COOKIE_DAYS);
        try { localStorage.setItem("st_" + k, val); } catch (e) {}
      }
      stored[k] = params.get(k) || getCookie("st_" + k) || (lsGet("st_" + k));
    });
    return stored;
  }
  function lsGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }

  // ---------------- FBP / FBC (Meta CAPI) ----------------
  function ensureFbp() {
    var fbp = getCookie("_fbp");
    if (!fbp) {
      fbp = "fb.1." + Date.now() + "." + Math.floor(Math.random() * 1e10);
      setCookie("_fbp", fbp, COOKIE_DAYS);
    }
    return fbp;
  }
  function ensureFbc() {
    var fbc = getCookie("_fbc");
    var params = new URLSearchParams(window.location.search);
    var fbclid = params.get("fbclid");
    if (fbclid) {
      fbc = "fb.1." + Date.now() + "." + fbclid;
      setCookie("_fbc", fbc, COOKIE_DAYS);
    }
    return getCookie("_fbc");
  }

  // ---------------- DECORA LINKS DE CHECKOUT ----------------
  function isCheckoutLink(href) {
    if (!href) return false;
    return CHECKOUT_HOSTS.some(function (h) { return href.indexOf(h) !== -1; });
  }
  function decorateLinks(data, fbp, fbc) {
    var anchors = document.querySelectorAll("a[href]");
    anchors.forEach(function (a) {
      if (!isCheckoutLink(a.href)) return;
      try {
        var url = new URL(a.href);
        UTM_KEYS.forEach(function (k) {
          if (data[k] && !url.searchParams.get(k)) url.searchParams.set(k, data[k]);
        });
        if (fbp) url.searchParams.set("fbp", fbp);
        if (fbc) url.searchParams.set("fbc", fbc);
        a.href = url.toString();
      } catch (e) {}
    });
  }

  // ---------------- ENVIO DE EVENTOS ----------------
  function sendEvent(type, data, fbp, fbc) {
    try {
      var payload = {
        event_type: type,
        url: window.location.href,
        fbp: fbp, fbc: fbc,
        utm_source: data.utm_source, utm_medium: data.utm_medium,
        utm_campaign: data.utm_campaign, utm_content: data.utm_content, utm_term: data.utm_term
      };
      var blob = new Blob([JSON.stringify(payload)], { type: "application/json" });
      if (navigator.sendBeacon) {
        navigator.sendBeacon(ENDPOINT + "/api/track", blob);
      } else {
        fetch(ENDPOINT + "/api/track", { method: "POST", body: JSON.stringify(payload), headers: { "Content-Type": "application/json" }, keepalive: true });
      }
    } catch (e) {}
  }

  // ---------------- API PUBLICA window.solutiontrack ----------------
  function init() {
    var data = captureUTMs();
    var fbp = ensureFbp();
    var fbc = ensureFbc();
    decorateLinks(data, fbp, fbc);
    sendEvent("PageView", data, fbp, fbc);

    // IC quando clicar em link de checkout
    document.addEventListener("click", function (e) {
      var a = e.target.closest ? e.target.closest("a[href]") : null;
      if (a && isCheckoutLink(a.href)) sendEvent("InitiateCheckout", data, fbp, fbc);
    }, true);

    window.solutiontrack = {
      data: data, fbp: fbp, fbc: fbc,
      track: function (type) { sendEvent(type, data, fbp, fbc); },
      getParams: function () { return data; }
    };
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
