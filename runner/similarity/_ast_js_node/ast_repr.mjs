// c-ast-js node helper (scheme §4 + 附录 A, BYTE-EXACT intent).
//
//   c-ast-js | acorn-loose 钉版, ecmaVersion=2023, 先 script 后 module;
//             前序遍历相邻 node-type 对 bigram 直方图 cosine;
//             解析失败 → token-bigram, 成对一致
//
// This helper turns each card's extracted JavaScript into the two candidate
// bigram representations the Python side needs to apply the pairwise-consistent
// fallback rule and the cosine:
//
//   ast_ok        : bool  — did acorn-loose parse (script-then-module)?
//   ast_bigrams   : {key:int}  — histogram of ADJACENT node-type pairs in the
//                                preorder-traversal type sequence
//   token_bigrams : {key:int}  — histogram of adjacent token-category pairs
//                                (self-contained tokenizer; the parse-failure
//                                 fallback, computed for every card so the
//                                 pairwise-consistent path is always available)
//
// Pinned: acorn-loose@8.4.0, acorn@8.14.0 (package.json --save-exact + lockfile).
//
// Protocol (batch): stdin  = JSON {"cards": {id: js_string, ...}}
//                   stdout = JSON {id: repr, ...}
// Bigram keys join the two symbols with U+0001 (absent from type/category names).

import * as looseParser from "acorn-loose";

const ECMA_VERSION = 2023;          // 附录 A: ecmaVersion=2023
const SOURCE_ORDER = ["script", "module"]; // 附录 A: 先 script 后 module
const SEP = "";               // bigram key separator

// -- AST preorder traversal ------------------------------------------------
// Preorder = visit node type, then recurse into child nodes in property-
// declaration order (deterministic for a pinned acorn version). start/end/type
// are the acorn bookkeeping fields; every other node- or array-valued property
// is a structural child. The resulting flat type sequence's ADJACENT pairs are
// the bigrams.
function preorderTypes(node, out) {
  if (!node || typeof node !== "object") return;
  if (typeof node.type === "string") out.push(node.type);
  for (const key of Object.keys(node)) {
    if (key === "type" || key === "start" || key === "end") continue;
    const v = node[key];
    if (Array.isArray(v)) {
      for (const e of v) {
        if (e && typeof e === "object" && typeof e.type === "string") {
          preorderTypes(e, out);
        }
      }
    } else if (v && typeof v === "object" && typeof v.type === "string") {
      preorderTypes(v, out);
    }
  }
}

function bigramsOf(seq) {
  const h = Object.create(null);
  for (let i = 0; i + 1 < seq.length; i++) {
    const key = seq[i] + SEP + seq[i + 1];
    h[key] = (h[key] || 0) + 1;
  }
  return h;
}

// -- acorn-loose parse: 先 script 后 module --------------------------------
// acorn-loose is error-tolerant and normally never throws; the try/catch and
// the module fallback exist for pathological inputs (e.g. RangeError on
// pathological nesting). ast_ok=false triggers the token-bigram fallback.
function astRepr(code) {
  for (const sourceType of SOURCE_ORDER) {
    try {
      const ast = looseParser.parse(code, {
        ecmaVersion: ECMA_VERSION,
        sourceType,
      });
      const types = [];
      preorderTypes(ast, types);
      return { ast_ok: true, source_type: sourceType, types };
    } catch (_e) {
      /* try next sourceType */
    }
  }
  return { ast_ok: false, source_type: null, types: [] };
}

// -- token-category tokenizer (parse-failure fallback) ---------------------
// Self-contained and total (never throws), so it is usable even when the AST
// parser itself failed. Emits a deterministic stream of token CATEGORY labels;
// adjacent-category bigrams form the fallback histogram. Regex-vs-division is
// not disambiguated (a `/` is always a punctuator) — an accepted approximation
// for a fallback path that the pinned acorn-loose parser essentially never hits.
const JS_KEYWORDS = new Set([
  "break","case","catch","class","const","continue","debugger","default",
  "delete","do","else","export","extends","finally","for","function","if",
  "import","in","instanceof","new","return","super","switch","this","throw",
  "try","typeof","var","void","while","with","yield","let","static","async",
  "await","of","get","set","true","false","null","undefined",
]);
const ID_START = /[A-Za-z_$\u0080-\uffff]/;
const ID_PART = /[A-Za-z0-9_$\u0080-\uffff]/;
const DIGIT = /[0-9]/;

function tokenCategories(code) {
  const cats = [];
  const n = code.length;
  let i = 0;
  while (i < n) {
    const c = code[i];
    // whitespace
    if (c === " " || c === "\t" || c === "\n" || c === "\r" || c === "\f" || c === "\v") { i++; continue; }
    // comments
    if (c === "/" && code[i + 1] === "/") { i += 2; while (i < n && code[i] !== "\n") i++; continue; }
    if (c === "/" && code[i + 1] === "*") {
      i += 2; while (i < n && !(code[i] === "*" && code[i + 1] === "/")) i++; i += 2; continue;
    }
    // strings
    if (c === '"' || c === "'") {
      const q = c; i++;
      while (i < n && code[i] !== q) { if (code[i] === "\\") i++; i++; }
      i++; cats.push("STR"); continue;
    }
    // template literals (naive; no nested ${} tokenizing)
    if (c === "`") {
      i++; while (i < n && code[i] !== "`") { if (code[i] === "\\") i++; i++; } i++;
      cats.push("TMPL"); continue;
    }
    // numbers (incl. hex/bin/oct prefixes, decimals, exponents — coarse)
    if (DIGIT.test(c) || (c === "." && DIGIT.test(code[i + 1] || ""))) {
      i++; while (i < n && /[0-9a-fA-FxXbBoO._eE+\-]/.test(code[i])) i++;
      cats.push("NUM"); continue;
    }
    // identifiers / keywords
    if (ID_START.test(c)) {
      let j = i + 1; while (j < n && ID_PART.test(code[j])) j++;
      const word = code.slice(i, j); i = j;
      cats.push(JS_KEYWORDS.has(word) ? "kw:" + word : "NAME");
      continue;
    }
    // punctuators — greedy match of the longest known operator
    const three = code.slice(i, i + 3);
    const four = code.slice(i, i + 4);
    if (four === ">>>=") { cats.push("op:" + four); i += 4; continue; }
    if (["===","!==","**=","<<=",">>=",">>>","&&=","||=","??=","..."].includes(three)) {
      cats.push("op:" + three); i += 3; continue;
    }
    const two = code.slice(i, i + 2);
    if (["==","!=","<=",">=","&&","||","??","?.","=>","++","--","+=","-=","*=","/=","%=","&=","|=","^=","**","<<",">>"].includes(two)) {
      cats.push("op:" + two); i += 2; continue;
    }
    cats.push("op:" + c); i++;
  }
  return cats;
}

// -- driver ----------------------------------------------------------------
function reprFor(code) {
  const ast = astRepr(code);
  const tokens = tokenCategories(code);
  return {
    ast_ok: ast.ast_ok,
    source_type: ast.source_type,
    n_nodes: ast.types.length,
    n_tokens: tokens.length,
    ast_bigrams: bigramsOf(ast.types),
    token_bigrams: bigramsOf(tokens),
  };
}

async function main() {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  const input = JSON.parse(Buffer.concat(chunks).toString("utf-8"));
  const cards = input.cards || {};
  const out = {};
  for (const id of Object.keys(cards)) out[id] = reprFor(String(cards[id]));
  process.stdout.write(JSON.stringify(out));
}

main().catch((e) => { process.stderr.write(String(e && e.stack || e)); process.exit(1); });
