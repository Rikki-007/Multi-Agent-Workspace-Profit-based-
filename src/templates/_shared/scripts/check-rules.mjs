// Enforces the measurable parts of RULES.md. Run via `npm run lint`.
// Vendored from src/templates/_shared/ — edit there, then `python -m agency.templates sync`.
// Which motion rules apply is read from this site's own dependencies, so one file serves every tier.
// A rule can be waived for one line with a comment containing `rules-allow: <rule>` and a reason.
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";

const ROOT = new URL("..", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1");
const allowDemo = process.argv.includes("--allow-demo");
const pkg = JSON.parse(readFileSync(join(ROOT, "package.json"), "utf8"));
const deps = { ...pkg.dependencies, ...pkg.devDependencies };
const usesLenis = "lenis" in deps;

const LAYOUT_PROPS =
  "width|height|top|left|right|bottom|inset|margin\\w*|padding\\w*|(?:max|min)(?:Width|Height)|fontSize|lineHeight|letterSpacing|gap|flexBasis|borderWidth";
const CSS_LAYOUT_PROPS =
  "width|height|top|left|right|bottom|inset|margin[\\w-]*|padding[\\w-]*|(?:max|min)-(?:width|height)|font-size|line-height|letter-spacing|gap|flex-basis|border-width";

function files(dir) {
  if (!existsSync(dir)) return [];
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const path = join(dir, e.name);
    if (e.isDirectory()) return ["node_modules", ".next", "out"].includes(e.name) ? [] : files(path);
    return /\.(tsx?|css)$/.test(e.name) ? [path] : [];
  });
}

// Text of a call's argument list, starting at the "(" index.
function callArgs(text, open) {
  let depth = 0;
  for (let i = open; i < text.length; i++) {
    if (text[i] === "(") depth++;
    else if (text[i] === ")" && --depth === 0) return text.slice(open + 1, i);
  }
  return text.slice(open + 1);
}

const problems = [];
const sources = files(join(ROOT, "src"));
let lenisInstances = 0;
let reducedMotionHandled = false;

for (const file of sources) {
  const text = readFileSync(file, "utf8");
  const rel = relative(ROOT, file).replaceAll("\\", "/");
  const lines = text.split("\n");
  const report = (index, rule, message) => {
    const line = text.slice(0, index).split("\n").length;
    if (!lines[line - 1].includes(`rules-allow: ${rule}`)) problems.push(`${rel}:${line}  [${rule}] ${message}`);
  };
  const each = (regex, fn) => {
    for (const m of text.matchAll(regex)) fn(m);
  };

  lenisInstances += (text.match(/new Lenis\(/g) || []).length;
  reducedMotionHandled ||= text.includes("prefers-reduced-motion") || text.includes("useReducedMotion");

  // Motion: GSAP tweens and Framer Motion props may only animate transform/opacity.
  each(/(?:\bgsap|\w+)\.(?:to|from|fromTo|set)\(/g, (m) => {
    const bad = callArgs(text, m.index + m[0].length - 1).match(new RegExp(`(?:^|[{,\\s])(${LAYOUT_PROPS})\\s*:`));
    if (bad) report(m.index, "transform-only", `tween animates "${bad[1]}"; use x/y/scale/opacity`);
  });
  each(/quickTo\([^,]+,\s*["'`](\w+)["'`]/g, (m) => {
    if (new RegExp(`^(${LAYOUT_PROPS})$`).test(m[1])) report(m.index, "transform-only", `quickTo animates "${m[1]}"`);
  });
  each(new RegExp(`\\b(?:initial|animate|exit|whileHover|whileTap|whileInView|whileFocus)=\\{\\{[^}]*\\b(${LAYOUT_PROPS})\\s*:`, "g"), (m) =>
    report(m.index, "transform-only", `motion prop animates "${m[1]}"`),
  );

  // Motion: CSS transitions/keyframes may only touch transform/opacity.
  each(/\btransition-all\b/g, (m) => report(m.index, "transform-only", "transition-all also animates layout; name the properties"));
  each(new RegExp(`transition(?:-property)?\\s*:[^;]*\\b(all|${CSS_LAYOUT_PROPS})\\b`, "g"), (m) =>
    report(m.index, "transform-only", `CSS transition on "${m[1]}"`),
  );
  each(/@keyframes[^{]+\{([\s\S]*?\})\s*\}/g, (m) => {
    const bad = m[1].match(new RegExp(`(?:^|[\\s;{])(${CSS_LAYOUT_PROPS})\\s*:`));
    if (bad) report(m.index, "transform-only", `@keyframes animates "${bad[1]}"`);
  });
  each(/addEventListener\(\s*["']scroll["']/g, (m) =>
    report(m.index, "scroll-listener", "use CSS scroll timelines, ScrollTrigger or whileInView instead"),
  );

  // Layout: no uniform card grids.
  each(/\b(?:[a-z0-9]+:)*grid-cols-(?:3|4)\b/g, (m) =>
    report(m.index, "uniform-grid", "uniform 3/4-column grid; use an asymmetric 12-column layout"),
  );

  // Images: sized (no layout shift) and described.
  if (rel.endsWith(".tsx")) {
    each(/<(img|Image)\b[\s\S]*?\/?>/g, (m) => {
      if (!/\balt=/.test(m[0])) report(m.index, "img-attrs", `<${m[1]}> is missing alt`);
      const sized = /\bwidth=/.test(m[0]) && /\bheight=/.test(m[0]);
      if (!sized && !(m[1] === "Image" && /\bfill\b/.test(m[0]))) report(m.index, "img-attrs", `<${m[1]}> needs width and height${m[1] === "Image" ? " or fill" : ""}`);
    });
  }
  each(/fonts\.googleapis\.com|use\.typekit\.net/g, (m) => report(m.index, "self-host-fonts", "load fonts with next/font"));

  // Content honesty.
  each(/lorem ipsum|your company|company name|john doe|placeholder text/gi, (m) =>
    report(m.index, "placeholder-copy", `placeholder copy "${m[0]}"`),
  );
  each(/★{3,}|\b(?:trusted by|loved by|rated)\s+[\d,.]+\s*[k+]?|\b\d{2,3}%\s+(?:satisfaction|of (?:our )?(?:clients|customers))/gi, (m) =>
    report(m.index, "unverified-claim", `"${m[0]}" must come from the spec; mark the line rules-allow: unverified-claim with its source`),
  );
  if (rel === "src/content.ts" && /^\s*demo:\s*true/m.test(text) && !allowDemo) {
    report(text.search(/demo:\s*true/), "demo-content", "content.ts still holds the template demo; fill it from the spec and set demo: false");
  }
}

if (usesLenis && lenisInstances !== 1) problems.push(`src  [lenis] expected exactly one \`new Lenis(\`, found ${lenisInstances}`);
if (!reducedMotionHandled) problems.push("src  [reduced-motion] nothing handles prefers-reduced-motion");

if (problems.length) {
  console.error(`check-rules: ${problems.length} problem(s)\n` + problems.map((p) => `  ${p}`).join("\n"));
  process.exit(1);
}
console.log(`check-rules: ${sources.length} files clean`);
