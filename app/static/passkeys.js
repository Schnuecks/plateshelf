// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 Schnuecks
// Passkeys (WebAuthn): Optionen vom Server holen, den Browser fragen, Antwort zurückschicken.
// Ohne WebAuthn im Browser (oder ohne HTTPS) bleiben die Knöpfe versteckt und ein Hinweis erscheint.
(() => {
  const supported = !!(window.PublicKeyCredential && navigator.credentials && window.isSecureContext);
  document.querySelectorAll("[data-passkey-unsupported]").forEach((el) => { el.hidden = supported; });
  if (!supported) return;

  const toBuf = (s) => {
    const b64 = s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4);
    return Uint8Array.from(atob(b64), (c) => c.charCodeAt(0)).buffer;
  };
  const toB64 = (buf) =>
    btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

  const creationOptions = (o) => ({
    ...o,
    challenge: toBuf(o.challenge),
    user: { ...o.user, id: toBuf(o.user.id) },
    excludeCredentials: (o.excludeCredentials || []).map((c) => ({ ...c, id: toBuf(c.id) })),
  });
  const requestOptions = (o) => ({
    ...o,
    challenge: toBuf(o.challenge),
    allowCredentials: (o.allowCredentials || []).map((c) => ({ ...c, id: toBuf(c.id) })),
  });

  const credentialJSON = (cred) => {
    const r = cred.response;
    const response = { clientDataJSON: toB64(r.clientDataJSON) };
    if (r.attestationObject) {
      response.attestationObject = toB64(r.attestationObject);
      if (r.getTransports) response.transports = r.getTransports();
    } else {
      response.authenticatorData = toB64(r.authenticatorData);
      response.signature = toB64(r.signature);
      if (r.userHandle) response.userHandle = toB64(r.userHandle);
    }
    return {
      id: cred.id,
      rawId: toB64(cred.rawId),
      type: cred.type,
      response,
      clientExtensionResults: cred.getClientExtensionResults ? cred.getClientExtensionResults() : {},
      authenticatorAttachment: cred.authenticatorAttachment || undefined,
    };
  };

  const post = (url, body) => fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify(body),
  });

  const run = async (button, optionsUrl, submitUrl, extra, create) => {
    const errorBox = document.querySelector("[data-passkey-error]");
    const show = (text) => { errorBox.textContent = text; errorBox.hidden = false; };
    errorBox.hidden = true;
    button.disabled = true;
    try {
      let resp = await post(optionsUrl, {});
      let data = await resp.json();
      if (!resp.ok) return show(data.error || errorBox.dataset.fallback);
      const cred = create
        ? await navigator.credentials.create({ publicKey: creationOptions(data.options) })
        : await navigator.credentials.get({ publicKey: requestOptions(data.options) });
      resp = await post(submitUrl, { ...extra(), token: data.token, credential: credentialJSON(cred) });
      data = await resp.json();
      if (!resp.ok) return show(data.error || errorBox.dataset.fallback);
      window.location.assign(data.redirect);
    } catch (err) {
      show(errorBox.dataset.cancelled);  // abgebrochen oder auf diesem Gerät nicht möglich
    } finally {
      button.disabled = false;
    }
  };

  document.querySelectorAll("[data-passkey-login]").forEach((button) => {
    button.hidden = false;
    button.addEventListener("click", () =>
      run(button, "/login/passkey/options", "/login/passkey", () => ({ next: button.dataset.next }), false));
  });

  document.querySelectorAll("form[data-passkey-register]").forEach((form) => {
    form.hidden = false;
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      run(form.querySelector("button"), "/account/passkeys/options", "/account/passkeys",
        () => ({ name: form.elements.name.value }), true);
    });
  });
})();
