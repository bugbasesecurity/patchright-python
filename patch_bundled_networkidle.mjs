// AST patch (ts-morph) of the BUNDLED patchright driver (coreBundle.js) to exclude
// captcha/analytics/heartbeat requests from networkidle. patchright-python ships a
// vanilla driver bundle + Python-layer stealth, so networkidle (a driver-level concept)
// must be added here at build time. This mirrors driver_patches/framesPatch.ts but runs
// against the already-bundled coreBundle.js: it finds the _inflightRequestStarted /
// _inflightRequestFinished methods, reads the real (bundler-renamed) param, and inserts
// the exclusion right after the existing `if (<param>._isFavicon) return;` early-return.
// No marker comment; hard-fails if an anchor is missing so upstream drift is caught.
import { Project, SyntaxKind } from "ts-morph";

const EXCLUDED = [
  "challenges.cloudflare.com", "google.com/recaptcha", "www.gstatic.com/recaptcha",
  "hcaptcha.com", "api.funcaptcha.com", "client-api.arkoselabs.com",
  "google-analytics.com", "googletagmanager.com", "analytics.google.com",
  "hotjar.com", "fullstory.com", "logrocket.com", "mouseflow.com", "clarity.ms",
  "browser-intake-datadoghq.com", "sentry.io", "newrelic.com", "nr-data.net",
  "forter.com", "/heartbeat", "/keepalive", "/keep-alive", "/beacon",
];

const target = process.argv[2];
if (!target) { console.error("usage: node patch_bundled_networkidle.mjs <coreBundle.js>"); process.exit(2); }

const project = new Project({ compilerOptions: { allowJs: true } });
const sf = project.addSourceFileAtPath(target);

function patchMethod(name) {
  const methods = sf.getDescendantsOfKind(SyntaxKind.MethodDeclaration)
    .filter((m) => m.getName() === name);
  let patched = 0;
  for (const m of methods) {
    const body = m.getBody();
    if (!body || body.getKind() !== SyntaxKind.Block) continue;
    const stmts = body.getStatements();
    const favIdx = stmts.findIndex((s) => s.getText().includes("._isFavicon"));
    if (favIdx === -1) continue; // not the frame-manager method
    if (body.getText().includes("some((p) => _reqUrl.includes(p))") ||
        body.getText().includes("challenges.cloudflare.com")) { patched++; continue; } // idempotent
    const param = m.getParameters()[0].getName();
    body.insertStatements(favIdx + 1, [
      `const _reqUrl = ${param}.url();`,
      `if (${JSON.stringify(EXCLUDED)}.some((p) => _reqUrl.includes(p))) return;`,
    ]);
    patched++;
  }
  if (patched === 0) throw new Error(`networkidle patch: no '${name}' method with an _isFavicon anchor found — driver structure changed`);
  return patched;
}

const a = patchMethod("_inflightRequestStarted");
const b = patchMethod("_inflightRequestFinished");
sf.saveSync();
console.log(`patched _inflightRequestStarted x${a}, _inflightRequestFinished x${b}`);
