import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const courseDir = path.dirname(fileURLToPath(import.meta.url));
const markdownPath = path.join(courseDir, "接口测试框架四周学习课程.md");
const outputPath = path.join(courseDir, "接口测试框架四周学习课程.html");
const lessonDirectoryName = "分阶段课程";
const lessonFileNames = new Map([
  [1, "第01天-项目边界与执行主线.html"],
  [2, "第02天-离线黄金路径与模块四件套.html"],
  [3, "第03天-模块标准结构与任务能力.html"],
  [4, "第04天-测试生命周期断言与报告步骤.html"],
  [5, "第05天-离线测试分层与分类用例.html"],
  [6, "第06天-基础请求与请求上下文.html"],
  [7, "第07天-请求中间件脱敏日志与资源捕获.html"],
  [8, "第08天-请求重试策略.html"],
  [9, "第09天-轮询状态机与流式响应.html"],
  [10, "第10天-测试上下文资源清理与并发隔离.html"],
  [11, "第11天-稳定入口与命令行参数.html"],
  [12, "第12天-权威收集与并串行调度.html"],
  [13, "第13天-执行器状态与退出码.html"],
  [14, "第14天-测试报告生命周期与机器产物.html"],
  [15, "第15天-可选质量能力与执行阶段.html"],
  [16, "第16天-运行时观察与质量事实采集.html"],
  [17, "第17天-质量归并语义与指标.html"],
  [18, "第18天-不稳定用例治理与产物信任.html"],
  [19, "第19天-流水线报告与持续集成.html"],
  [20, "第20天-综合实践与结业评审.html"],
]);

const markdown = fs.readFileSync(markdownPath, "utf8");
const rendered = renderMarkdown(markdown);
const documentHtml = renderDocument(rendered);

fs.writeFileSync(outputPath, documentHtml, "utf8");
process.stdout.write(`Generated ${outputPath}\n`);

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
  value = value.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  return value;
}

function lessonRoute(headingText) {
  const match = headingText.match(/^Day\s+(\d+)：/i);
  if (!match) return null;
  const day = Number(match[1]);
  const fileName = lessonFileNames.get(day);
  if (!fileName) return null;
  const absolutePath = path.join(courseDir, lessonDirectoryName, fileName);
  return {
    day,
    fileName,
    href: encodeURI(`${lessonDirectoryName}/${fileName}`),
    exists: fs.existsSync(absolutePath),
  };
}

function splitTableRow(line) {
  return line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
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
    if (
      level === 1 &&
      (plain.startsWith("接口测试框架") || plain.startsWith("API Test Framework"))
    ) {
      return uniqueId("top");
    }
    if ((match = plain.match(/^第(\d+)周：/))) return uniqueId(`week-${match[1]}`);
    if ((match = plain.match(/^Day\s+(\d+)：/i))) return uniqueId(`day-${match[1]}`);
    if ((match = plain.match(/^第(\d+)周门禁/))) return uniqueId(`week-${match[1]}-gate`);
    if ((match = plain.match(/^(\d+)\.\s*/))) {
      const sectionNames = {
        1: "course-positioning",
        2: "first-principles",
        3: "toc-strategy",
        4: "learning-rules",
        5: "course-overview",
        6: "knowledge-checklist",
        7: "notes-template",
        8: "assessment",
        9: "usage-guide",
      };
      return uniqueId(sectionNames[Number(match[1])] ?? `section-${match[1]}`);
    }
    generatedId += 1;
    return uniqueId(`topic-${generatedId}`);
  }

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];

    if (codeFence) {
      if (/^```\s*$/.test(line)) {
        const languageClass = codeFence ? ` class="language-${escapeHtml(codeFence)}"` : "";
        html.push(`<pre><code${languageClass}>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
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
      html.push(
        `<h${level} id="${id}" tabindex="-1"><a class="heading-anchor" href="#${id}" aria-label="跳转到本节">#</a>${renderInline(text)}</h${level}>`,
      );
      if (lesson) {
        html.push(
          `<div class="lesson-entry"><a href="${escapeHtml(lesson.href)}">进入第${lesson.day}天课程</a><span class="lesson-status ${lesson.exists ? "ready" : "pending"}">${lesson.exists ? "已生成" : "待生成"}</span></div>`,
        );
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

    if (
      line.includes("|") &&
      index + 1 < lines.length &&
      isTableSeparator(lines[index + 1])
    ) {
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
        html.push(
          `<li class="task-item"><label><input type="checkbox" data-progress-id="item-${checklistId}"${checked}> <span>${renderInline(taskMatch[2])}</span></label></li>`,
        );
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
  if (codeFence) {
    html.push(`<pre><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
  }

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

function renderDocument({ body, headings, checklistCount }) {
  const navigation = renderNavigation(headings);
  return `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="API Test Framework 四周学习课程导航">
  <title>接口测试框架四周学习课程</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f4f7fb;
      --surface: #ffffff;
      --surface-soft: #eef4ff;
      --text: #172033;
      --muted: #5e6a7d;
      --line: #d9e1ec;
      --accent: #2563eb;
      --accent-strong: #1746b3;
      --code-bg: #111827;
      --code-text: #e5edf8;
      --sidebar-width: 320px;
      --shadow: 0 14px 40px rgba(36, 51, 79, .09);
    }

    * { box-sizing: border-box; }
    html { scroll-behavior: smooth; scroll-padding-top: 24px; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 16px/1.72 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
    }
    a { color: var(--accent); text-decoration: none; }
    a:hover { color: var(--accent-strong); text-decoration: underline; }

    .layout { min-height: 100vh; }
    .sidebar {
      position: fixed;
      inset: 0 auto 0 0;
      z-index: 20;
      width: var(--sidebar-width);
      padding: 24px 18px;
      overflow-y: auto;
      background: #0f172a;
      color: #e5e7eb;
      box-shadow: 8px 0 30px rgba(15, 23, 42, .15);
    }
    .brand { padding: 0 10px 18px; border-bottom: 1px solid #2b3850; }
    .brand-kicker { margin: 0 0 5px; color: #93c5fd; font-size: 12px; letter-spacing: .12em; text-transform: uppercase; }
    .brand-title { margin: 0; color: #fff; font-size: 20px; line-height: 1.35; }
    .brand-meta { margin: 8px 0 0; color: #aeb8c9; font-size: 13px; }
    .progress-box { margin: 18px 10px; }
    .progress-label { display: flex; justify-content: space-between; margin-bottom: 7px; color: #cbd5e1; font-size: 12px; }
    .progress-track { height: 7px; overflow: hidden; border-radius: 99px; background: #334155; }
    .progress-value { width: 0; height: 100%; border-radius: inherit; background: linear-gradient(90deg, #38bdf8, #60a5fa); transition: width .2s ease; }
    .toc { display: grid; gap: 2px; padding: 6px 0 40px; }
    .toc-link { display: block; padding: 7px 10px; border-radius: 7px; color: #cbd5e1; font-size: 13px; line-height: 1.35; }
    .toc-link:hover { background: #1e293b; color: #fff; text-decoration: none; }
    .toc-link.active { background: #1d4ed8; color: #fff; }
    .toc-level-1 { margin-top: 8px; color: #fff; font-weight: 700; }
    .toc-level-2 { padding-left: 22px; }

    .main { margin-left: var(--sidebar-width); padding: 36px 42px 72px; }
    .toolbar { display: flex; justify-content: flex-end; gap: 10px; max-width: 1040px; margin: 0 auto 18px; }
    .toolbar a, .menu-button {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 8px 12px;
      background: var(--surface);
      color: var(--muted);
      font: inherit;
      font-size: 13px;
      box-shadow: 0 3px 12px rgba(36, 51, 79, .05);
    }
    .menu-button { display: none; cursor: pointer; }
    .content {
      max-width: 1040px;
      margin: 0 auto;
      padding: 52px 68px 80px;
      border: 1px solid #e5eaf1;
      border-radius: 16px;
      background: var(--surface);
      box-shadow: var(--shadow);
    }

    h1, h2, h3, h4 { position: relative; line-height: 1.35; letter-spacing: -.015em; }
    h1 { margin: 2.8em 0 .8em; padding-top: .2em; font-size: 32px; }
    h1:first-child { margin-top: 0; font-size: 40px; }
    h2 { margin: 2.4em 0 .7em; padding-bottom: .35em; border-bottom: 1px solid var(--line); font-size: 25px; }
    h3 { margin: 1.8em 0 .5em; font-size: 19px; }
    h4 { margin: 1.5em 0 .4em; font-size: 17px; }
    .heading-anchor { position: absolute; right: 100%; padding-right: 8px; color: #9aa7b8; opacity: 0; font-weight: 400; }
    h1:hover .heading-anchor, h2:hover .heading-anchor, h3:hover .heading-anchor, h4:hover .heading-anchor, .heading-anchor:focus { opacity: 1; text-decoration: none; }
    p { margin: .8em 0 1.05em; }
    ul, ol { margin: .6em 0 1.2em; padding-left: 1.6em; }
    li { margin: .32em 0; }
    hr { margin: 3em 0; border: 0; border-top: 1px solid var(--line); }
    strong { color: #101827; }
    code {
      border-radius: 5px;
      padding: .12em .38em;
      background: #edf2f7;
      color: #b42354;
      font: 0.9em/1.5 "Cascadia Code", Consolas, monospace;
    }
    pre { margin: 1.1em 0 1.5em; overflow-x: auto; border-radius: 10px; padding: 18px 20px; background: var(--code-bg); box-shadow: inset 0 1px rgba(255,255,255,.05); }
    pre code { padding: 0; background: transparent; color: var(--code-text); font-size: 13px; }
    .lesson-entry { display: flex; align-items: center; gap: 10px; margin: -6px 0 22px; }
    .lesson-entry > a { display: inline-flex; align-items: center; border-radius: 8px; padding: 7px 12px; background: var(--accent); color: #fff; font-size: 14px; font-weight: 650; }
    .lesson-entry > a:hover { background: var(--accent-strong); color: #fff; text-decoration: none; }
    .lesson-status { border-radius: 99px; padding: 2px 8px; font-size: 12px; }
    .lesson-status.ready { background: #dcfce7; color: #166534; }
    .lesson-status.pending { background: #f1f5f9; color: #64748b; }
    .lesson-toc-link { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: 8px; }
    .lesson-toc-link small { color: #94a3b8; font-size: 10px; white-space: nowrap; }
    .lesson-toc-link:hover small { color: #bfdbfe; }
    .table-wrap { margin: 1.2em 0 1.8em; overflow-x: auto; border: 1px solid var(--line); border-radius: 10px; }
    table { width: 100%; border-collapse: collapse; background: var(--surface); }
    th, td { padding: 11px 14px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
    th { background: var(--surface-soft); color: #25324a; font-size: 14px; }
    tr:last-child td { border-bottom: 0; }
    .task-item { list-style: none; margin-left: -1.5em; }
    .task-item label { display: flex; align-items: flex-start; gap: 8px; cursor: pointer; }
    .task-item input { margin-top: .48em; accent-color: var(--accent); }
    .task-item input:checked + span { color: var(--muted); text-decoration: line-through; }
    .back-to-top { position: fixed; right: 24px; bottom: 24px; display: grid; place-items: center; width: 42px; height: 42px; border: 1px solid var(--line); border-radius: 50%; background: var(--surface); box-shadow: var(--shadow); font-size: 18px; }

    @media (max-width: 980px) {
      .sidebar { transform: translateX(-105%); transition: transform .2s ease; }
      body.nav-open .sidebar { transform: translateX(0); }
      .main { margin-left: 0; padding: 18px 18px 56px; }
      .menu-button { display: inline-block; }
      .toolbar { justify-content: space-between; }
      .content { padding: 38px 28px 64px; }
    }
    @media (max-width: 560px) {
      .content { padding: 30px 20px 52px; border-radius: 10px; }
      h1:first-child { font-size: 32px; }
      h1 { font-size: 28px; }
      h2 { font-size: 22px; }
      .back-to-top { right: 14px; bottom: 14px; }
    }
    @media print {
      .sidebar, .toolbar, .back-to-top { display: none !important; }
      .main { margin: 0; padding: 0; }
      .content { max-width: none; padding: 0; border: 0; box-shadow: none; }
      a { color: inherit; }
      pre { white-space: pre-wrap; }
    }
  </style>
</head>
<body>
  <div class="layout">
    <aside class="sidebar" id="sidebar" aria-label="课程目录">
      <div class="brand">
        <p class="brand-kicker">接口测试框架课程</p>
        <p class="brand-title">四周框架学习路线</p>
        <p class="brand-meta">20个学习日 · 4个阶段门禁</p>
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
        <div>
          <a href="接口测试框架四周学习课程.md" target="_blank" rel="noopener">查看课程源文件</a>
        </div>
      </div>
      <article class="content">${body}</article>
    </main>
  </div>
  <a class="back-to-top" href="#top" aria-label="返回顶部">↑</a>
  <script>
    (() => {
      const storageKey = "api-case-course-progress-v1";
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
  </script>
</body>
</html>
`;
}
