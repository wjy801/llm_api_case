import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const courseDir = path.dirname(fileURLToPath(import.meta.url));
const markdownPath = path.join(courseDir, "接口测试框架六周学习课程.md");
const outputPath = path.join(courseDir, "接口测试框架六周学习课程.html");
const lessonDirectoryName = "分阶段课程";
const lessonFileNames = new Map([
  [1, "第01天-项目边界与执行主线.html"],
  [2, "第02天-离线黄金路径与模块四件套.html"],
  [3, "第03天-模块标准结构与任务能力.html"],
  [4, "第04天-测试生命周期断言与报告步骤.html"],
  [5, "第05天-离线测试分层与分类用例.html"],
  [6, "第06天-基础请求与请求上下文.html"],
  [7, "第07天-请求中间件脱敏与诊断日志.html"],
  [8, "第08天-资源捕获下载与原子落盘.html"],
  [9, "第09天-请求重试策略.html"],
  [10, "第10天-轮询状态机与总截止时间.html"],
  [11, "第11天-流式响应与资源生命周期.html"],
  [12, "第12天-测试上下文提取与资源清理.html"],
  [13, "第13天-上下文传播与并发隔离.html"],
  [14, "第14天-稳定入口与命令行参数.html"],
  [15, "第15天-权威收集与并串行调度.html"],
  [16, "第16天-执行器状态与退出码.html"],
  [17, "第17天-测试报告生命周期与机器产物.html"],
  [18, "第18天-运行时观察接口.html"],
  [19, "第19天-可选质量能力与执行阶段.html"],
  [20, "第20天-质量适配与运行事实采集.html"],
  [21, "第21天-质量事实归并与完整性.html"],
  [22, "第22天-语义关系与所有权完整性.html"],
  [23, "第23天-指标来源验证与解释.html"],
  [24, "第24天-不稳定用例身份周期与导入.html"],
  [25, "第25天-不稳定用例状态与治理.html"],
  [26, "第26天-Artifact读取Hash与Manifest信任.html"],
  [27, "第27天-流水线报告事实与多视图.html"],
  [28, "第28天-持续集成编排与发布边界.html"],
  [29, "第29天-离线场景跨层实现追踪.html"],
  [30, "第30天-结业答辩与同轮证据验收.html"],
]);

export function generateSixWeekCourse() {
  validateLessonPages();
  const lessonStatus = getLessonStatus();
  if (!fs.existsSync(markdownPath)) {
    if (!fs.existsSync(outputPath)) {
      throw new Error(`six-week Markdown source was removed and generated overview is missing: ${outputPath}`);
    }
    process.stdout.write(`Validated existing ${outputPath} (${lessonStatus.ready}/${lessonStatus.total} lessons ready; Markdown source removed)\n`);
    return { outputPath, ...lessonStatus, generated: false };
  }
  const markdown = fs.readFileSync(markdownPath, "utf8");
  validateLessonContract(markdown);
  const rendered = renderMarkdown(markdown);
  fs.writeFileSync(outputPath, renderDocument(rendered, lessonStatus), "utf8");
  process.stdout.write(`Generated ${outputPath} (${lessonStatus.ready}/${lessonStatus.total} lessons ready)\n`);
  return { outputPath, ...lessonStatus, generated: true };
}

function validateLessonContract(markdown) {
  const requiredMarkers = [
    "# 接口测试框架源码架构与证据链六周课程",
    "### Day 0准备度检查",
    "## 4. 统一术语与关系索引",
    "### 5.3 六维评分量表",
    "## Day 8：资源捕获下载与原子落盘",
    "## Day 18：运行时观察接口",
    "## Day 19：可选质量能力与执行阶段",
    "## Day 26：Artifact读取Hash与Manifest信任",
  ];
  for (const marker of requiredMarkers) {
    if (!markdown.includes(marker)) {
      throw new Error(`six-week course missing second-round contract marker: ${marker}`);
    }
  }
  if (!markdown.includes('P0事实形成后分成两条硬依赖支线')) {
    throw new Error('six-week course must document the branched Week 5 dependency model');
  }
  if (!markdown.includes('每课只要求一个主成果')) {
    throw new Error('six-week course must document the one-primary-artifact output policy');
  }
  assertNoLinearFlakyDependency(markdown, path.basename(markdownPath));
  const reviewCount = (markdown.match(/\*\*周复盘30～45分钟\*\*/g) ?? []).length;
  if (reviewCount !== 5) {
    throw new Error(`six-week course must contain five weekly review blocks, found ${reviewCount}`);
  }
  const expectedDays = Array.from({ length: 30 }, (_, index) => index + 1);
  const days = [...lessonFileNames.keys()];
  if (days.length !== 30 || days.some((day, index) => day !== expectedDays[index])) {
    throw new Error("lessonFileNames must map Day 1 through Day 30 continuously");
  }
  const fileNames = [...lessonFileNames.values()];
  if (new Set(fileNames).size !== fileNames.length) {
    throw new Error("lessonFileNames must not contain duplicate file names");
  }
  const headings = [...markdown.matchAll(/^## Day\s+(\d+)：(.+)$/gm)].map((match) => ({
    day: Number(match[1]),
    title: match[2].trim(),
  }));
  if (headings.length !== 30) {
    throw new Error(`six-week course must contain 30 Day headings, found ${headings.length}`);
  }
  for (const [index, heading] of headings.entries()) {
    const expectedDay = index + 1;
    const expectedTitle = lessonFileNames.get(expectedDay).replace(/^第\d+天-/, "").replace(/\.html$/, "");
    if (heading.day !== expectedDay || heading.title !== expectedTitle) {
      throw new Error(`Day ${expectedDay} contract mismatch: expected "${expectedTitle}", got Day ${heading.day} "${heading.title}"`);
    }
  }
}

function validateLessonPages() {
  const sharedScriptPath = path.join(courseDir, lessonDirectoryName, '六周课程页.js');
  const sharedScript = fs.readFileSync(sharedScriptPath, 'utf8');
  if (!sharedScript.includes('one-primary-artifact.v1')) {
    throw new Error('six-week lesson pages must enable the one-primary-artifact output policy');
  }

  const primaryEvidencePattern = /data-(?:record|note)=(?:'|\x22)(?:gate-evidence|gate-record|gate-output-location)(?:'|\x22)/g;
  const reviewFields = new Map([
    [5, 'review-map'],
    [10, 'review-map'],
    [15, 'week3-summary'],
    [20, 'week4-summary'],
    [25, 'review-quality-chain'],
    [30, 'course-causal-map'],
  ]);

  for (const [day, fileName] of lessonFileNames) {
    const lessonPath = path.join(courseDir, lessonDirectoryName, fileName);
    const html = fs.readFileSync(lessonPath, 'utf8');
    const primaryEvidenceCount = (html.match(primaryEvidencePattern) ?? []).length;
    if (primaryEvidenceCount !== 1) {
      throw new Error(`Day ${day} must contain exactly one primary evidence field, found ${primaryEvidenceCount}`);
    }
    const reviewField = reviewFields.get(day);
    if (reviewField) {
      const reviewFieldPattern = new RegExp(`data-(?:note|record)=(?:'|\\x22)${reviewField}(?:'|\\x22)`);
      if (!reviewFieldPattern.test(html)) {
        throw new Error(`Day ${day} is missing its integrated weekly review field: ${reviewField}`);
      }
    }
    if (html.includes('review-map,review-correction,review-transfer,review-terms')) {
      throw new Error(`Day ${day} still requires four independent weekly review outputs`);
    }
    assertNoLinearFlakyDependency(html, fileName);
  }

  const day23 = fs.readFileSync(path.join(courseDir, lessonDirectoryName, lessonFileNames.get(23)), 'utf8');
  if (!day23.includes('不是Day24 Flaky导入的前置')) {
    throw new Error('Day 23 must state that Metrics is not a prerequisite for Day 24 Flaky import');
  }
  const day24 = fs.readFileSync(path.join(courseDir, lessonDirectoryName, lessonFileNames.get(24)), 'utf8');
  if (!day24.includes('不依赖Semantic或Metrics')) {
    throw new Error('Day 24 must state that Flaky import does not depend on Semantic or Metrics');
  }
}

function assertNoLinearFlakyDependency(content, sourceName) {
  const plainText = content.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');
  const forbiddenChains = [
    'P0 → Semantic → Metrics → Flaky',
    'P0 → Semantic → Metrics → Flaky identity → governance',
    'P0 fact merge → final run record → semantic → metrics → flaky history',
  ];
  for (const chain of forbiddenChains) {
    if (plainText.toLowerCase().includes(chain.toLowerCase())) {
      throw new Error(`${sourceName} incorrectly models Flaky as downstream of Semantic or Metrics: ${chain}`);
    }
  }
}

function getLessonStatus() {
  const missing = [];
  for (const [day, fileName] of lessonFileNames) {
    if (!fs.existsSync(path.join(courseDir, lessonDirectoryName, fileName))) {
      missing.push({ day, fileName });
    }
  }
  return { total: lessonFileNames.size, ready: lessonFileNames.size - missing.length, missing };
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function plainHeading(value) {
  return value
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/\[([^\]]+)]\([^)]+\)/g, "$1")
    .trim();
}

function renderInline(rawValue) {
  let value = escapeHtml(rawValue);
  value = value.replace(/`([^`]+)`/g, "<code>$1</code>");
  value = value.replace(
    /\[([^\]]+)]\(([^)]+)\)/g,
    (_match, label, href) => `<a href="${escapeHtml(href)}">${label}</a>`,
  );
  return value.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
}

function lessonRoute(headingText) {
  const match = headingText.match(/^Day\s+(\d+)：(.+)$/i);
  if (!match) return null;
  const day = Number(match[1]);
  const fileName = lessonFileNames.get(day);
  if (!fileName) return null;
  return {
    day,
    href: encodeURI(`${lessonDirectoryName}/${fileName}`),
    exists: fs.existsSync(path.join(courseDir, lessonDirectoryName, fileName)),
  };
}

function splitTableRow(line) {
  return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim());
}

function isTableSeparator(line) {
  const cells = splitTableRow(line);
  return cells.length > 0 && cells.every((cell) => /^:?-{3,}:?$/.test(cell));
}

function renderMarkdown(source) {
  const lines = source.replaceAll("\r\n", "\n").split("\n");
  const html = [];
  const headings = [];
  const usedIds = new Map();
  let generatedId = 0;
  let checklistId = 0;
  let paragraph = [];
  let listType = null;
  let codeFence = null;
  let codeLines = [];

  function closeParagraph() {
    if (paragraph.length === 0) return;
    html.push(`<p>${renderInline(paragraph.join(" "))}</p>`);
    paragraph = [];
  }

  function closeList() {
    if (!listType) return;
    html.push(`</${listType}>`);
    listType = null;
  }

  function uniqueId(base) {
    const count = usedIds.get(base) ?? 0;
    usedIds.set(base, count + 1);
    return count === 0 ? base : `${base}-${count + 1}`;
  }

  function headingId(text, level) {
    const plain = plainHeading(text);
    let match;
    if (level === 1 && plain.startsWith("接口测试框架")) return uniqueId("top");
    if ((match = plain.match(/^第(\d+)周：/))) return uniqueId(`week-${match[1]}`);
    if ((match = plain.match(/^Day\s+(\d+)：/i))) return uniqueId(`day-${match[1]}`);
    if ((match = plain.match(/^第(\d+)周门禁/))) return uniqueId(`week-${match[1]}-gate`);
    if ((match = plain.match(/^(\d+)\.\s*/))) return uniqueId(`section-${match[1]}`);
    generatedId += 1;
    return uniqueId(`topic-${generatedId}`);
  }

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    if (codeFence) {
      if (/^```\s*$/.test(line)) {
        html.push(`<pre><code class="language-${escapeHtml(codeFence)}">${escapeHtml(codeLines.join("\n"))}</code></pre>`);
        codeFence = null;
        codeLines = [];
      } else {
        codeLines.push(line);
      }
      continue;
    }

    const fenceMatch = line.match(/^```\s*([\w+-]*)\s*$/);
    if (fenceMatch) {
      closeParagraph();
      closeList();
      codeFence = fenceMatch[1] || "text";
      codeLines = [];
      continue;
    }

    const headingMatch = line.match(/^(#{1,6})\s+(.+)$/);
    if (headingMatch) {
      closeParagraph();
      closeList();
      const level = headingMatch[1].length;
      const text = headingMatch[2].trim();
      const plainText = plainHeading(text);
      const id = headingId(text, level);
      const lesson = level === 2 ? lessonRoute(plainText) : null;
      html.push(`<h${level} id="${id}" tabindex="-1"><a class="heading-anchor" href="#${id}" aria-label="跳转到本节">#</a>${renderInline(text)}</h${level}>`);
      if (lesson) {
        html.push(`<div class="lesson-entry"><a href="${escapeHtml(lesson.href)}">进入第${lesson.day}天课程</a><span class="lesson-status ${lesson.exists ? "ready" : "pending"}">${lesson.exists ? "已生成" : "待生成"}</span></div>`);
      }
      if (level <= 2) headings.push({ level, id, text: plainText, lesson });
      continue;
    }

    if (/^\s*---+\s*$/.test(line)) {
      closeParagraph();
      closeList();
      html.push("<hr>");
      continue;
    }

    if (line.includes("|") && index + 1 < lines.length && isTableSeparator(lines[index + 1])) {
      closeParagraph();
      closeList();
      const headers = splitTableRow(line);
      index += 2;
      const rows = [];
      while (index < lines.length && lines[index].includes("|") && lines[index].trim()) {
        rows.push(splitTableRow(lines[index]));
        index += 1;
      }
      index -= 1;
      html.push('<div class="table-wrap"><table><thead><tr>');
      for (const header of headers) html.push(`<th>${renderInline(header)}</th>`);
      html.push("</tr></thead><tbody>");
      for (const row of rows) {
        html.push("<tr>");
        for (let cellIndex = 0; cellIndex < headers.length; cellIndex += 1) {
          html.push(`<td>${renderInline(row[cellIndex] ?? "")}</td>`);
        }
        html.push("</tr>");
      }
      html.push("</tbody></table></div>");
      continue;
    }

    const taskMatch = line.match(/^\s*[-*]\s+\[([ xX])]\s+(.+)$/);
    const unorderedMatch = line.match(/^\s*[-*]\s+(.+)$/);
    const orderedMatch = line.match(/^\s*\d+\.\s+(.+)$/);
    if (taskMatch || unorderedMatch || orderedMatch) {
      closeParagraph();
      const desiredList = orderedMatch ? "ol" : "ul";
      if (listType !== desiredList) {
        closeList();
        listType = desiredList;
        html.push(`<${listType}>`);
      }
      if (taskMatch) {
        checklistId += 1;
        const checked = taskMatch[1].trim() ? " checked" : "";
        html.push(`<li class="task-item"><label><input type="checkbox" data-progress-id="item-${checklistId}"${checked}> <span>${renderInline(taskMatch[2])}</span></label></li>`);
      } else {
        html.push(`<li>${renderInline((orderedMatch ?? unorderedMatch)[1])}</li>`);
      }
      continue;
    }

    if (!line.trim()) {
      closeParagraph();
      closeList();
      continue;
    }
    paragraph.push(line.trim());
  }

  closeParagraph();
  closeList();
  if (codeFence) html.push(`<pre><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
  return { body: html.join("\n"), headings, checklistCount: checklistId };
}

function renderNavigation(headings) {
  return headings
    .filter((heading) => heading.id !== "top")
    .map((heading) => {
      if (heading.lesson) {
        return `<a class="toc-link toc-level-${heading.level} lesson-toc-link" href="${escapeHtml(heading.lesson.href)}"><span>${escapeHtml(heading.text)}</span><small>${heading.lesson.exists ? "已生成" : "待生成"}</small></a>`;
      }
      return `<a class="toc-link toc-level-${heading.level}" href="#${heading.id}" data-target="${heading.id}">${escapeHtml(heading.text)}</a>`;
    })
    .join("\n");
}

function renderDocument({ body, headings, checklistCount }, lessonStatus) {
  const navigation = renderNavigation(headings);
  const readiness = `${lessonStatus.ready}/${lessonStatus.total}个日课已生成`;
  return `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="接口测试框架源码架构与证据链六周课程：基于现有项目实现学习调用链、状态、资源所有权与机器证据">
  <title>接口测试框架源码架构与证据链六周课程</title>
  <style>${renderStyles()}</style>
</head>
<body data-course-contract="six-week-v2">
  <div class="layout">
    <aside class="sidebar" id="sidebar" aria-label="课程目录">
      <div class="brand">
        <p class="brand-kicker">源码架构与证据链</p>
        <p class="brand-title">接口测试框架六周学习路线</p>
        <p class="brand-meta">30个学习日 · 6个阶段门禁</p>
        <p class="brand-ready">${readiness}</p>
      </div>
      <div class="progress-box"${checklistCount ? "" : " hidden"}>
        <div class="progress-label"><span>知识清单进度</span><span id="progress-text">0/${checklistCount}</span></div>
        <div class="progress-track"><div class="progress-value" id="progress-value"></div></div>
      </div>
      <nav class="toc" id="toc">${navigation}</nav>
    </aside>
    <main class="main">
      <div class="toolbar">
        <button class="menu-button" id="menu-button" type="button" aria-controls="sidebar" aria-expanded="false">☰ 课程目录</button>
        <span>当前总纲HTML</span>
      </div>
      <article class="content">${body}</article>
    </main>
  </div>
  <a class="back-to-top" href="#top" aria-label="返回顶部">↑</a>
  <script>${renderClientScript()}</script>
</body>
</html>
`;
}

function renderStyles() {
  return `
    :root { color-scheme: light; --bg:#f4f7fb; --surface:#fff; --soft:#eef4ff; --text:#172033; --muted:#5e6a7d; --line:#d9e1ec; --accent:#2563eb; --accent-strong:#1746b3; --code:#111827; --sidebar:330px; }
    * { box-sizing:border-box; }
    .sr-only { position:absolute; width:1px; height:1px; padding:0; margin:-1px; overflow:hidden; clip:rect(0,0,0,0); white-space:nowrap; border:0; }
    html { scroll-behavior:smooth; scroll-padding-top:24px; }
    body { margin:0; background:var(--bg); color:var(--text); font:16px/1.72 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif; }
    a { color:var(--accent); text-decoration:none; }
    a:hover { color:var(--accent-strong); text-decoration:underline; }
    .layout { min-height:100vh; }
    .sidebar { position:fixed; inset:0 auto 0 0; z-index:20; width:var(--sidebar); overflow-y:auto; padding:26px 20px; background:#0f172a; color:#e2e8f0; box-shadow:8px 0 28px rgba(15,23,42,.12); }
    .brand { padding:0 10px 20px; border-bottom:1px solid #253047; }
    .brand p { margin:0; }
    .brand-kicker { color:#7dd3fc; font-size:12px; letter-spacing:.12em; text-transform:uppercase; }
    .brand-title { margin-top:7px!important; color:#fff; font-size:21px; font-weight:750; }
    .brand-meta,.brand-ready { margin-top:5px!important; color:#94a3b8; font-size:12px; }
    .progress-box { margin:18px 10px; }
    .progress-label { display:flex; justify-content:space-between; margin-bottom:7px; color:#cbd5e1; font-size:12px; }
    .progress-track { height:6px; overflow:hidden; border-radius:99px; background:#26344c; }
    .progress-value { width:0; height:100%; background:linear-gradient(90deg,#38bdf8,#60a5fa); transition:width .2s ease; }
    .toc { display:grid; gap:2px; padding:6px 0 40px; }
    .toc-link { display:block; padding:7px 10px; border-radius:7px; color:#cbd5e1; font-size:13px; line-height:1.35; }
    .toc-link:hover { background:#1e293b; color:#fff; text-decoration:none; }
    .toc-link.active { background:#1d4ed8; color:#fff; }
    .toc-level-1 { margin-top:8px; color:#fff; font-weight:700; }
    .toc-level-2 { padding-left:22px; }
    .lesson-toc-link { display:grid; grid-template-columns:minmax(0,1fr) auto; gap:8px; align-items:center; }
    .lesson-toc-link small { color:#94a3b8; font-size:10px; white-space:nowrap; }
    .main { margin-left:var(--sidebar); padding:36px 42px 72px; }
    .toolbar { display:flex; justify-content:flex-end; gap:10px; max-width:1080px; margin:0 auto 18px; }
    .toolbar a,.menu-button { border:1px solid var(--line); border-radius:8px; padding:8px 12px; background:var(--surface); color:var(--muted); font:inherit; font-size:13px; box-shadow:0 3px 12px rgba(36,51,79,.05); }
    .menu-button { display:none; cursor:pointer; }
    .content { max-width:1080px; margin:0 auto; padding:52px 68px 80px; border:1px solid #e5eaf1; border-radius:16px; background:var(--surface); box-shadow:0 14px 40px rgba(36,51,79,.09); }
    h1,h2,h3,h4 { position:relative; line-height:1.35; letter-spacing:-.015em; }
    h1 { margin:2.8em 0 .8em; padding-top:.2em; font-size:32px; }
    h1:first-child { margin-top:0; font-size:40px; }
    h2 { margin:2.4em 0 .7em; padding-bottom:.35em; border-bottom:1px solid var(--line); font-size:25px; }
    h3 { margin:1.8em 0 .5em; font-size:19px; }
    .heading-anchor { position:absolute; right:100%; padding-right:8px; color:#9aa7b8; opacity:0; font-weight:400; }
    h1:hover .heading-anchor,h2:hover .heading-anchor,h3:hover .heading-anchor,.heading-anchor:focus { opacity:1; text-decoration:none; }
    p { margin:.8em 0 1.05em; }
    ul,ol { margin:.6em 0 1.2em; padding-left:1.6em; }
    li { margin:.32em 0; }
    hr { margin:3em 0; border:0; border-top:1px solid var(--line); }
    code { border-radius:5px; padding:.12em .38em; background:#edf2f7; color:#b42354; font:.9em/1.5 "Cascadia Code",Consolas,monospace; }
    pre { margin:1.1em 0 1.5em; overflow-x:auto; border-radius:10px; padding:18px 20px; background:var(--code); color:#e5edf8; }
    pre code { padding:0; background:transparent; color:inherit; font-size:13px; }
    .lesson-entry { display:flex; align-items:center; gap:10px; margin:-6px 0 22px; }
    .lesson-entry>a { border-radius:8px; padding:7px 12px; background:var(--accent); color:#fff; font-size:14px; font-weight:650; }
    .lesson-status { border-radius:99px; padding:2px 8px; font-size:12px; }
    .lesson-status.ready { background:#dcfce7; color:#166534; }
    .lesson-status.pending { background:#f1f5f9; color:#64748b; }
    .table-wrap { margin:1.2em 0 1.8em; overflow-x:auto; border:1px solid var(--line); border-radius:10px; }
    table { width:100%; border-collapse:collapse; }
    th,td { padding:11px 14px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; }
    th { background:var(--soft); color:#25324a; font-size:14px; }
    tr:last-child td { border-bottom:0; }
    .glossary-tools { position:sticky; top:10px; z-index:5; display:grid; grid-template-columns:minmax(220px,1fr) 190px; gap:10px; margin:1em 0 1.5em; padding:12px; border:1px solid var(--line); border-radius:10px; background:rgba(255,255,255,.96); box-shadow:0 6px 20px rgba(36,51,79,.08); }
    .glossary-tools input,.glossary-tools select { width:100%; border:1px solid var(--line); border-radius:7px; padding:9px 10px; background:#fff; color:var(--text); font:inherit; }
    .glossary-empty { display:none; margin:0 0 1.5em; border-radius:8px; padding:10px 12px; background:#fff7ed; color:#9a3412; }
    .task-item { list-style:none; margin-left:-1.5em; }
    .task-item label { display:flex; align-items:flex-start; gap:8px; cursor:pointer; }
    .task-item input { margin-top:.48em; accent-color:var(--accent); }
    .task-item input:checked+span { color:var(--muted); text-decoration:line-through; }
    .back-to-top { position:fixed; right:24px; bottom:24px; display:grid; place-items:center; width:42px; height:42px; border:1px solid var(--line); border-radius:50%; background:#fff; box-shadow:0 8px 24px rgba(36,51,79,.12); }
    @media (max-width:980px) { .sidebar { transform:translateX(-100%); transition:transform .2s ease; } body.nav-open .sidebar { transform:none; } .main { margin-left:0; padding:20px; } .menu-button { display:inline-block; } .toolbar { justify-content:space-between; } .content { padding:38px 28px 64px; } }
    @media (max-width:560px) { .content { padding:30px 20px 52px; border-radius:10px; } h1:first-child { font-size:32px; } h1 { font-size:28px; } h2 { font-size:22px; } .glossary-tools { position:static; grid-template-columns:1fr; } .back-to-top { right:14px; bottom:14px; } }
    @media print { .sidebar,.toolbar,.back-to-top { display:none!important; } .main { margin:0; padding:0; } .content { max-width:none; padding:0; border:0; box-shadow:none; } pre { white-space:pre-wrap; } }
  `;
}

function renderClientScript() {
  return `
    (() => {
      const storageKey = "api-case-course-progress-v2";
      const checks = [...document.querySelectorAll("[data-progress-id]")];
      const progressText = document.getElementById("progress-text");
      const progressValue = document.getElementById("progress-value");
      let saved = {};
      try { saved = JSON.parse(localStorage.getItem(storageKey) || "{}"); } catch (_) { saved = {}; }

      function updateProgress() {
        const completed = checks.filter((item) => item.checked).length;
        if (progressText) progressText.textContent = completed + "/" + checks.length;
        if (progressValue) progressValue.style.width = (checks.length ? completed / checks.length * 100 : 0) + "%";
      }

      for (const check of checks) {
        if (Object.prototype.hasOwnProperty.call(saved, check.dataset.progressId)) {
          check.checked = Boolean(saved[check.dataset.progressId]);
        }
        check.addEventListener("change", () => {
          saved[check.dataset.progressId] = check.checked;
          localStorage.setItem(storageKey, JSON.stringify(saved));
          updateProgress();
        });
      }
      updateProgress();

      const glossaryHeading = document.getElementById("section-4");
      if (glossaryHeading) {
        const tools = document.createElement("div");
        tools.className = "glossary-tools";
        tools.innerHTML = '<label><span class="sr-only">搜索术语</span><input id="glossary-search" type="search" placeholder="搜索术语、所有权、上下游或证据"></label><label><span class="sr-only">按层过滤</span><select id="glossary-layer"><option value="all">全部层级</option><option value="business">业务与用例层</option><option value="common">公共能力层</option><option value="runner">Runner与执行层</option><option value="quality">Quality层</option><option value="reporting">Reporting与CI层</option></select></label>';
        glossaryHeading.insertAdjacentElement("afterend", tools);
        const empty = document.createElement("p");
        empty.className = "glossary-empty";
        empty.textContent = "没有匹配术语，请清除搜索词或切换层级。";
        tools.insertAdjacentElement("afterend", empty);
        const groups = [];
        let current = null;
        let node = empty.nextElementSibling;
        while (node && node.tagName !== "H2") {
          if (node.tagName === "H3") {
            const text = node.textContent;
            const layer = text.includes("业务") ? "business" : text.includes("公共") ? "common" : text.includes("Runner") ? "runner" : text.includes("Quality") ? "quality" : "reporting";
            current = { heading: node, layer, wrappers: [], rows: [] };
            groups.push(current);
          } else if (current && node.classList.contains("table-wrap")) {
            current.wrappers.push(node);
            current.rows.push(...node.querySelectorAll("tbody tr"));
          }
          node = node.nextElementSibling;
        }
        const search = document.getElementById("glossary-search");
        const layer = document.getElementById("glossary-layer");
        const applyGlossaryFilter = () => {
          const query = search.value.trim().toLowerCase();
          let visibleRows = 0;
          for (const group of groups) {
            let groupVisible = 0;
            for (const row of group.rows) {
              const visible = (layer.value === "all" || layer.value === group.layer) && (!query || row.textContent.toLowerCase().includes(query));
              row.hidden = !visible;
              if (visible) groupVisible += 1;
            }
            group.heading.hidden = groupVisible === 0;
            for (const wrapper of group.wrappers) wrapper.hidden = groupVisible === 0;
            visibleRows += groupVisible;
          }
          empty.style.display = visibleRows ? "none" : "block";
        };
        search.addEventListener("input", applyGlossaryFilter);
        layer.addEventListener("change", applyGlossaryFilter);
      }

      const menuButton = document.getElementById("menu-button");
      menuButton?.addEventListener("click", () => {
        const open = document.body.classList.toggle("nav-open");
        menuButton.setAttribute("aria-expanded", String(open));
      });

      const tocLinks = [...document.querySelectorAll(".toc-link[data-target]")];
      const linkById = new Map(tocLinks.map((link) => [link.dataset.target, link]));
      const observed = [...linkById.keys()].map((id) => document.getElementById(id)).filter(Boolean);
      const observer = new IntersectionObserver((entries) => {
        const visible = entries.filter((entry) => entry.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (!visible.length) return;
        for (const link of tocLinks) link.classList.remove("active");
        linkById.get(visible[0].target.id)?.classList.add("active");
      }, { rootMargin: "-8% 0px -78% 0px", threshold: [0, 1] });
      for (const heading of observed) observer.observe(heading);

      document.getElementById("toc")?.addEventListener("click", () => {
        document.body.classList.remove("nav-open");
        menuButton?.setAttribute("aria-expanded", "false");
      });
    })();
  `;
}

const invokedUrl = process.argv[1] ? pathToFileURL(path.resolve(process.argv[1])).href : null;
if (invokedUrl === import.meta.url) {
  generateSixWeekCourse();
}
