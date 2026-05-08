"""Encrypt every HTML file in dist/ with AES-256-GCM, password-gated.

This is client-side encryption: the rendered output on Pages is the cipher-
text, and the browser decrypts it after the user enters the password. It is
NOT server-side authentication — the encrypted blob is publicly served and
anyone can attempt brute-force decryption. With a short password like
"BAUM0L" the cost of brute force is low (a few hours on a single GPU).
Treat this as a friction barrier, not real security.

Algorithm:
- 256-bit AES-GCM
- PBKDF2-HMAC-SHA256 with 250 000 iterations to derive the key
- One random salt per build (shared across all pages — so PBKDF2 runs once
  per session, then the derived key is cached in sessionStorage)
- One random IV per page

Page wrapper: a small HTML page containing the password prompt and the
decryption JavaScript using browser-native `crypto.subtle`. On successful
decrypt, the page replaces document.documentElement.outerHTML with the
plaintext, so the original site renders unchanged. Internal navigation
between encrypted pages reuses the cached derived key automatically.
"""
from __future__ import annotations

import base64
import os
import secrets
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


PBKDF2_ITERATIONS = 250_000
KEY_LEN = 32  # 256-bit key


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def derive_key(password: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=KEY_LEN,
                     salt=salt, iterations=PBKDF2_ITERATIONS)
    return kdf.derive(password.encode("utf-8"))


def encrypt_html(plaintext: str, key: bytes) -> tuple[str, str]:
    """Encrypt one HTML payload. Returns (iv_b64, ciphertext_b64)."""
    iv = secrets.token_bytes(12)
    aesgcm = AESGCM(key)
    ct = aesgcm.encrypt(iv, plaintext.encode("utf-8"), None)
    return _b64(iv), _b64(ct)


WRAPPER_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
:root {{
  --ink:#1a1a1a; --muted:#555; --soft:#f6f4ee; --line:#ddd;
  --accent:#b30000; --serif: ui-serif, Georgia, "Times New Roman", serif;
  --sans: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}}
* {{ box-sizing:border-box; }}
body {{
  margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
  font-family:var(--serif); color:var(--ink); background:#fff;
}}
@media (prefers-color-scheme: dark) {{
  body {{ background:#111; color:#ececec; }}
  .gate {{ background:#1c1c1c; border-color:#333; }}
}}
.gate {{
  max-width: 420px; width: 90%; padding: 1.5rem 1.7rem 1.4rem;
  border:1px solid var(--line); border-radius:6px; background:var(--soft);
}}
.gate h1 {{ margin:0 0 0.4rem; font-size:1.2rem; letter-spacing:-0.01em; }}
.gate p {{ margin:0.4rem 0; font-family:var(--sans); font-size:0.92rem; color:var(--muted); line-height:1.5; }}
.gate form {{ margin-top:1.0rem; display:flex; gap:0.4rem; }}
.gate input[type=password] {{
  flex:1; padding:0.5rem 0.6rem; font-family:var(--sans); font-size:1rem;
  border:1px solid #999; border-radius:4px; background:#fff; color:var(--ink);
}}
.gate button {{
  padding:0.5rem 0.9rem; font-family:var(--sans); font-size:0.95rem;
  border:0; background:var(--accent); color:#fff; border-radius:4px; cursor:pointer;
}}
.gate button:hover {{ filter: brightness(1.05); }}
.gate .err {{ color:var(--accent); font-size:0.88rem; margin-top:0.5rem; min-height:1.1em; }}
.gate small {{ display:block; margin-top:0.9rem; color:var(--muted); font-size:0.78rem; font-family:var(--sans); }}
</style>
</head>
<body>
<noscript>
  <p>This page is encrypted. JavaScript must be enabled to view it.</p>
</noscript>
<div class="gate" id="gate">
  <h1>Password required</h1>
  <p>This page is part of a draft analysis at
    <code>baumol.stephenkinsella.net</code> and is gated until publication.</p>
  <form id="gateForm" autocomplete="off">
    <input type="password" id="pw" placeholder="Password" autocomplete="off"
           autofocus aria-label="Password">
    <button type="submit">Unlock</button>
  </form>
  <div class="err" id="err"></div>
  <small>Encrypted with AES-256-GCM in your browser. The page never leaves
    encrypted form on the server.</small>
</div>
<script>
(function () {{
  const SALT_B64  = "{salt_b64}";
  const IV_B64    = "{iv_b64}";
  const CT_B64    = "{ct_b64}";
  const ITER      = {iterations};
  const KEY_LEN   = {key_len_bytes};

  function b64ToBytes(b64) {{
    const bin = atob(b64); const out = new Uint8Array(bin.length);
    for (let i=0;i<bin.length;i++) out[i] = bin.charCodeAt(i);
    return out;
  }}

  async function deriveKeyFromPassword(password, saltBytes) {{
    const enc = new TextEncoder();
    const baseKey = await crypto.subtle.importKey(
      "raw", enc.encode(password), {{name:"PBKDF2"}}, false, ["deriveKey"]
    );
    return crypto.subtle.deriveKey(
      {{name:"PBKDF2", salt:saltBytes, iterations:ITER, hash:"SHA-256"}},
      baseKey, {{name:"AES-GCM", length:KEY_LEN*8}}, true, ["decrypt"]
    );
  }}

  async function tryDecrypt(key) {{
    try {{
      const ivBytes = b64ToBytes(IV_B64);
      const ctBytes = b64ToBytes(CT_B64);
      const ptBuf = await crypto.subtle.decrypt(
        {{name:"AES-GCM", iv:ivBytes}}, key, ctBytes
      );
      const html = new TextDecoder().decode(ptBuf);
      replaceDocument(html);
      return true;
    }} catch (e) {{
      return false;
    }}
  }}

  function replaceDocument(html) {{
    // Replace the entire document with the decrypted HTML, keeping the
    // current URL.
    document.open();
    document.write(html);
    document.close();
  }}

  async function unlock(password) {{
    const saltBytes = b64ToBytes(SALT_B64);
    const key = await deriveKeyFromPassword(password, saltBytes);
    const exported = await crypto.subtle.exportKey("raw", key);
    if (await tryDecrypt(key)) {{
      try {{
        sessionStorage.setItem(
          "baumol_key_v1",
          btoa(String.fromCharCode.apply(null, new Uint8Array(exported)))
        );
      }} catch (_) {{}}
      return true;
    }}
    return false;
  }}

  async function tryCachedKey() {{
    let cached = null;
    try {{ cached = sessionStorage.getItem("baumol_key_v1"); }} catch (_) {{}}
    if (!cached) return false;
    try {{
      const raw = b64ToBytes(cached);
      const key = await crypto.subtle.importKey(
        "raw", raw, {{name:"AES-GCM", length:KEY_LEN*8}}, false, ["decrypt"]
      );
      return await tryDecrypt(key);
    }} catch (_) {{ return false; }}
  }}

  window.addEventListener("DOMContentLoaded", async () => {{
    if (await tryCachedKey()) return;
    const form = document.getElementById("gateForm");
    const pw = document.getElementById("pw");
    const err = document.getElementById("err");
    form.addEventListener("submit", async (ev) => {{
      ev.preventDefault();
      err.textContent = "";
      const password = pw.value;
      if (!password) return;
      err.textContent = "Decrypting...";
      const ok = await unlock(password);
      if (!ok) {{
        err.textContent = "Incorrect password.";
        pw.select();
      }}
    }});
  }});
}})();
</script>
</body>
</html>
"""


def encrypt_dist(dist_dir: Path, password: str) -> dict:
    salt = secrets.token_bytes(16)
    key = derive_key(password, salt)
    salt_b64 = _b64(salt)

    encrypted = []
    for html_path in sorted(dist_dir.rglob("*.html")):
        plaintext = html_path.read_text(encoding="utf-8")
        # Pull a title out for the gate page (best-effort)
        title = "Encrypted — Baumol & Ireland"
        m = plaintext.find("<title>")
        if m != -1:
            end = plaintext.find("</title>", m)
            if end != -1:
                title = plaintext[m + 7:end].strip()
        iv_b64, ct_b64 = encrypt_html(plaintext, key)
        wrapper = WRAPPER_TEMPLATE.format(
            title=_html_escape(title),
            salt_b64=salt_b64,
            iv_b64=iv_b64,
            ct_b64=ct_b64,
            iterations=PBKDF2_ITERATIONS,
            key_len_bytes=KEY_LEN,
        )
        html_path.write_text(wrapper, encoding="utf-8")
        encrypted.append(html_path.name)

    return {"count": len(encrypted), "salt_b64": salt_b64,
            "iterations": PBKDF2_ITERATIONS, "files": encrypted}


def _html_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", default="dist")
    parser.add_argument("--password", default=os.environ.get("SITE_PASSWORD"))
    args = parser.parse_args()
    if not args.password:
        raise SystemExit(
            "No password supplied. Set SITE_PASSWORD env var or pass --password."
        )
    summary = encrypt_dist(Path(args.dist), args.password)
    print(f"Encrypted {summary['count']} HTML files in {args.dist}/")
    print(f"PBKDF2 salt (b64): {summary['salt_b64']}")
    print(f"Iterations: {summary['iterations']}")
