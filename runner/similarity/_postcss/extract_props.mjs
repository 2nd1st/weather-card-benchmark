// c-css-prop declaration extractor (v12 附录 A: "postcss 钉版；property 名频次分布 cosine").
//
// Contract:
//   stdin  = JSON array of CSS source strings (one per <style> block extracted from card.html)
//   stdout = JSON object { "props": [<prop name>, ...], "parsed": <int>, "failed": <int> }
//
// Each block is parsed independently with pinned postcss (see package.json). We walk *all*
// declarations (recursing into @media / @keyframes / @supports / :root / nested rules) and emit
// decl.prop verbatim (postcss preserves case; custom properties like --foo are declarations too).
// A block that fails to parse is skipped and counted in `failed`; parseable blocks still contribute.
import postcss from 'postcss';

let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (d) => { input += d; });
process.stdin.on('end', () => {
  let blocks;
  try {
    blocks = JSON.parse(input);
  } catch (e) {
    process.stdout.write(JSON.stringify({ props: [], parsed: 0, failed: 0, error: 'bad-input' }));
    return;
  }
  const props = [];
  let parsed = 0;
  let failed = 0;
  for (const css of blocks) {
    try {
      const root = postcss.parse(css, { from: undefined });
      root.walkDecls((decl) => { props.push(decl.prop); });
      parsed += 1;
    } catch (e) {
      failed += 1;
    }
  }
  process.stdout.write(JSON.stringify({ props, parsed, failed }));
});
