/** HMAC request signing — mirror of backend/app/edge/signing.py. */

const enc = new TextEncoder();

async function hmacHex(secret: string, data: string): Promise<string> {
  const key = await crypto.subtle.importKey("raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = await crypto.subtle.sign("HMAC", key, enc.encode(data));
  return [...new Uint8Array(sig)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function sha256Hex(body: Uint8Array): Promise<string> {
  const d = await crypto.subtle.digest("SHA-256", body);
  return [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export async function signHeaders(secret: string, method: string, path: string, body: Uint8Array, ts = Math.floor(Date.now() / 1000)) {
  const canonical = `${ts}.${method.toUpperCase()}.${path}.${await sha256Hex(body)}`;
  return { "x-havoc-ts": String(ts), "x-havoc-sig": await hmacHex(secret, canonical) };
}

export async function verifySignature(secret: string, method: string, path: string, body: Uint8Array, ts: string | null, sig: string | null) {
  if (!ts || !sig || Math.abs(Date.now() / 1000 - Number(ts)) > 300) return false;
  const expected = (await signHeaders(secret, method, path, body, Number(ts)))["x-havoc-sig"];
  if (expected.length !== sig.length) return false;
  let diff = 0;
  for (let i = 0; i < expected.length; i++) diff |= expected.charCodeAt(i) ^ sig.charCodeAt(i);
  return diff === 0;
}
