/**
 * dsh-memory — 把本地经验库（memory_onefile.py）开放成通用工具。
 *
 * 设计目标：**任何模型**（DeepSeek / GPT / Claude / Qwen / GLM …）在 DSH 里都能直接用，
 * 不依赖某个厂商的技能格式，也不注入模型专属指令。所有能力都是普通 Tool：
 *
 *   memory_search   检索跨会话经验（只读）
 *   memory_get      按 id 取一条经验的完整内容（只读）
 *   memory_status   记忆库概览（只读）
 *   memory_save     沉淀一条经验（写，默认请求用户批准）
 *   memory_feedback 回写经验效果（写，默认请求用户批准）
 *
 * 后端仍是同一份 Python 单文件实现与同一套 JSON 数据：脚本随包内置（vendor/），
 * 也可用 config.scriptPath 指向你自己的副本；数据目录由 config.dataDir 决定。
 *
 * 本文件只依赖 Node 内置模块，不 import 任何 Harness 包，保证在 profile 安装后即可解析。
 *
 * @module @local/dsh-memory
 */
import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { homedir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

export const name = 'dsh-memory'
export const inject = ['tools']

const HERE = dirname(fileURLToPath(import.meta.url))
const VENDORED_SCRIPT = join(HERE, 'memory_onefile.py')
/** 前端星图读取的只读数据路由。 */
const STAR_MAP_PATH = '/dsh-memory/starmap.json'
/** 星图上「批准/驳回」按钮的写路由（唯一写入口，人点才触发）。 */
const PROPOSAL_ACTION_PATH = '/dsh-memory/proposals'

const DEFAULTS = {
  /** Python 可执行文件；空 = 按平台选 python / python3。 */
  pythonExe: '',
  /** 记忆脚本路径；空 = 用包内的 vendor/memory_onefile.py。 */
  scriptPath: '',
  /** 数据目录；空 = DSH_MEMORY_DATA_DIR / MEMORY_DATA_DIR → %LOCALAPPDATA%\doubao-memory\data。 */
  dataDir: '',
  /** 单次命令超时（毫秒）。 */
  timeoutMs: 30000,
  /** search 默认返回条数上限。 */
  maxResults: 6,
  /** 允许写工具（save/feedback）。 */
  allowWrites: true,
  /** 写工具执行前请求用户批准。 */
  requireApproval: true,
  /** 注入一小段系统提示，告诉模型何时查/何时存。 */
  promptHint: true,
  /** 允许星图界面上的「批准/驳回」按钮（唯一写路由）。 */
  allowUiWrites: true,
}

const PROMPT_HINT = [
  '长期记忆：跨会话的经验可以用 memory_search 检索（先看 premise 适用前提与 counter 反面坑；',
  '冲突经验成对看；stale 的提示可能过时）。检索为空就直说“记忆里没有相关经验”，不要编造。',
  '用户确认后才用 memory_save 沉淀；按经验办完事后用 memory_feedback 回写效果。',
].join('')

const SUMMARY_MAX = 160
const FIELD_MAX = 140
const DETAIL_MAX = 4000

/** 把配置和默认值合并，并对类型做一次兜底。 */
function resolveConfig(raw) {
  const cfg = { ...DEFAULTS, ...(raw && typeof raw === 'object' ? raw : {}) }
  cfg.timeoutMs = Number.isFinite(cfg.timeoutMs) && cfg.timeoutMs > 0 ? cfg.timeoutMs : DEFAULTS.timeoutMs
  cfg.maxResults = Number.isFinite(cfg.maxResults) && cfg.maxResults > 0 ? cfg.maxResults : DEFAULTS.maxResults
  cfg.allowWrites = cfg.allowWrites !== false
  cfg.requireApproval = cfg.requireApproval !== false
  cfg.promptHint = cfg.promptHint !== false
  return cfg
}

/** 解析数据目录：显式配置 > 环境变量 > 与桌面版共用的稳定目录。 */
function resolveDataDir(cfg) {
  if (cfg.dataDir) return cfg.dataDir
  const fromEnv = process.env.DSH_MEMORY_DATA_DIR || process.env.MEMORY_DATA_DIR
  if (fromEnv) return fromEnv
  const base = process.env.LOCALAPPDATA || join(homedir(), '.local', 'share')
  return join(base, 'doubao-memory', 'data')
}

/** 解析要执行的脚本：显式配置且存在则用之，否则用包内副本。 */
function resolveScript(cfg) {
  if (cfg.scriptPath && existsSync(cfg.scriptPath)) return cfg.scriptPath
  return VENDORED_SCRIPT
}

const VENDORED_STARMAP = join(HERE, 'starmap_dump.py')

/** 星图数据：一次性导出记忆库结构 JSON（只读，不落盘）。 */
async function loadStarmap(cfg) {
  const { stdout } = await runCommand(cfg, [], undefined, {
    script: VENDORED_STARMAP,
    label: 'starmap_dump',
  })
  return parseJson(stdout)
}

function pythonExe(cfg) {
  if (cfg.pythonExe) return cfg.pythonExe
  return process.platform === 'win32' ? 'python' : 'python3'
}

/** 截断并显式标注省略，避免把长篇原文塞进上下文。 */
function clip(text, max) {
  const value = String(text ?? '').replace(/\s+/g, ' ').trim()
  return value.length > max ? `${value.slice(0, max)}…(已截断)` : value
}

/**
 * 运行一次记忆命令。
 * @param {object} cfg 解析后的配置。
 * @param {string[]} argv 子命令及其参数。
 * @param {AbortSignal|undefined} signal 工具调用的取消信号。
 * @param {{script?: string, label?: string}} [options] 覆盖脚本或错误标签（星图导出用）。
 * @returns {Promise<{stdout: string, stderr: string}>} 命令输出。
 */
function runCommand(cfg, argv, signal, options = {}) {
  const script = options.script || resolveScript(cfg)
  const label = options.label || 'memory_onefile'
  if (!existsSync(script)) {
    return Promise.reject(new Error(`找不到记忆脚本：${script}（可在插件配置里设置 scriptPath）`))
  }
  const exe = pythonExe(cfg)
  const dataDir = resolveDataDir(cfg)
  return new Promise((resolvePromise, rejectPromise) => {
    let settled = false
    const child = spawn(exe, [script, ...argv], {
      cwd: dirname(script),
      env: {
        ...process.env,
        MEMORY_DATA_DIR: dataDir,
        PYTHONIOENCODING: 'utf-8',
        PYTHONUTF8: '1',
      },
      windowsHide: true,
      stdio: ['ignore', 'pipe', 'pipe'],
    })
    let stdout = ''
    let stderr = ''
    const finish = (error, value) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      if (error) rejectPromise(error)
      else resolvePromise(value)
    }
    const timer = setTimeout(() => {
      child.kill('SIGKILL')
      finish(new Error(`记忆命令超时（${cfg.timeoutMs}ms）：${label} ${argv.slice(0, 2).join(' ')}`))
    }, cfg.timeoutMs)
    const onAbort = () => {
      child.kill('SIGKILL')
      finish(new Error('记忆命令已取消'))
    }
    if (signal) {
      if (signal.aborted) onAbort()
      else signal.addEventListener('abort', onAbort, { once: true })
    }
    child.stdout.setEncoding('utf-8')
    child.stderr.setEncoding('utf-8')
    child.stdout.on('data', (chunk) => { stdout += chunk })
    child.stderr.on('data', (chunk) => { stderr += chunk })
    child.on('error', (error) => {
      if (error && error.code === 'ENOENT') {
        finish(new Error(`找不到 Python 可执行文件「${exe}」：请确认已安装 Python，或在插件配置里设置 pythonExe`))
        return
      }
      finish(new Error(`无法启动记忆命令：${error?.message ?? error}`))
    })
    child.on('close', (code) => {
      if (signal) signal.removeEventListener('abort', onAbort)
      if (code === 0) {
        finish(undefined, { stdout, stderr })
        return
      }
      const tail = (stderr || stdout || '').trim().split(/\r?\n/).slice(-6).join('\n')
      finish(new Error(`记忆命令失败（exit ${code}）：${clip(tail, 600)}`))
    })
  })
}

/** 解析 --json 输出；解析失败时抛出具名错误，避免把异常 JSON 当数据用。 */
function parseJson(stdout) {
  const text = String(stdout || '').trim()
  const start = text.indexOf('{')
  if (start < 0) throw new Error('记忆命令没有返回 JSON')
  return JSON.parse(text.slice(start))
}

/** search 单条命中 → 面向模型的紧凑对象。 */
function toHit(hit) {
  return {
    id: hit.id,
    summary: clip(hit.summary, SUMMARY_MAX),
    relevance: hit.relevance ?? null,
    confidence: hit.score ?? null,
    maturity: hit.maturity ?? 'draft',
    maturityLabel: hit.maturity_label ?? '',
    caseType: hit.case_type ?? 'neutral',
    tags: Array.isArray(hit.tags) ? hit.tags : [],
    premise: clip(hit.premise, FIELD_MAX),
    counter: clip(hit.counter, FIELD_MAX),
    conflicts: Array.isArray(hit.conflict_ids) ? hit.conflict_ids : [],
    sourceRef: clip(hit.source_ref, FIELD_MAX),
    ageDays: hit.age_days ?? null,
    stale: hit.stale === true,
    pinned: hit.pinned === true,
    source: hit.source ?? 'main',
  }
}

/** 一条命中 → 一段可读文本。 */
function hitLines(hit, index) {
  const badges = [
    `相关 ${hit.relevance ?? 0}`,
    `可信 ${hit.confidence ?? 0}`,
    hit.maturityLabel || hit.maturity,
    hit.caseType,
    hit.pinned ? '🔒重要' : '',
    hit.stale ? `可能过时(${hit.ageDays}天)` : '',
  ].filter(Boolean)
  const lines = [`${index}. [${hit.source}] ${hit.id} · ${badges.join(' · ')}`, `   ${hit.summary}`]
  if (hit.premise) lines.push(`   前提: ${hit.premise}`)
  if (hit.counter) lines.push(`   坑/限: ${hit.counter}`)
  if (hit.conflicts.length > 0) lines.push(`   冲突: ${hit.conflicts.join(', ')}（成对呈现，交给用户判断）`)
  if (hit.sourceRef) lines.push(`   溯源: ${hit.sourceRef}`)
  return lines.join('\n')
}

/** 写操作前请求用户批准；没有审批服务或没有 agent 时失败关闭。 */
async function ensureApproval(ctx, exec, cfg, summary) {
  if (!cfg.requireApproval) return
  const approval = ctx.get('approval')
  if (!approval) {
    throw new Error('当前环境没有审批服务，写入被拒绝（可在插件配置里设置 requireApproval: false）')
  }
  if (!exec.agent) {
    throw new Error('当前调用没有绑定会话，无法请求用户批准写入')
  }
  let outcome
  try {
    outcome = await approval.request({
      agent: exec.agent,
      toolName: exec.name,
      callId: exec.callId,
      reason: summary,
      displayReason: {
        en: `Write to the long-term memory store: ${summary}`,
        zh: `写入长期记忆库：${summary}`,
      },
      signal: exec.signal,
    })
  } catch (error) {
    throw new Error(`请求用户批准失败：${error?.message ?? error}`)
  }
  if (outcome !== 'allowed-once') {
    throw new Error(
      outcome === 'rejected'
        ? '用户拒绝了这次记忆写入（若每次都被直接拒绝，可能是会话审批策略为 never：改成 ask，'
          + '或把本插件配置 requireApproval 设为 false）'
        : `记忆写入未获批准（${outcome}）`,
    )
  }
}

function requireWrites(cfg) {
  if (!cfg.allowWrites) throw new Error('写入工具已禁用（插件配置 allowWrites: false）')
}

/** 统一的文本输出定义：value 是结构化结果，render 给模型看的紧凑文本。 */
function textOutput(schemaBuilder) {
  return {
    schema: schemaBuilder(),
    render: (_args, value) => [{ type: 'text', text: String(value?.text ?? '') }],
  }
}

const MEMORY_OBJ_PROPS = {
  id: { type: 'string' },
  summary: { type: 'string' },
  relevance: { type: 'number' },
  confidence: { type: 'number' },
  maturity: { type: 'string' },
  maturityLabel: { type: 'string' },
  caseType: { type: 'string' },
  tags: { type: 'array', items: { type: 'string' } },
  premise: { type: 'string' },
  counter: { type: 'string' },
  conflicts: { type: 'array', items: { type: 'string' } },
  sourceRef: { type: 'string' },
  ageDays: { type: 'integer' },
  stale: { type: 'boolean' },
  pinned: { type: 'boolean' },
  source: { type: 'string' },
}

/** 输出 schema 用标准 JSON Schema：`required` 是对象层数组，不能挂在标量节点上。 */
const MEMORY_ITEM_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: MEMORY_OBJ_PROPS,
  required: ['id', 'summary'],
}

/**
 * 注册全部记忆工具。
 * @param {object} ctx Cordis 上下文（含 tools，可选 approval / systemPrompt）。
 * @param {object|undefined} raw 插件行配置。
 */
export function apply(ctx, raw) {
  const cfg = resolveConfig(raw)

  const tools = [
    {
      name: 'memory_search',
      description:
        '检索本地长期经验库（跨会话）。当用户开始新任务、提到“上次/以前/我之前/那个教训”，'
        + '或需要判断有没有相关经验时先调用。返回每条经验的相关度、可信度、成熟度、适用前提(premise)、'
        + '反面坑(counter)、冲突标记与溯源，以及是否属于信息缺口。没有命中就明确说“记忆里没有”，不要编造。',
      parameters: {
        query: {
          type: 'string',
          required: true,
          description: '检索词：一句话或几个关键词，用空格分隔。中文直接写短语，不要加引号。',
        },
        topK: {
          type: 'integer',
          description: `最多返回几条，1~20，默认 ${DEFAULTS.maxResults}。`,
        },
      },
      output: textOutput(() => ({
        type: 'object',
        additionalProperties: false,
        properties: {
          text: { type: 'string' },
          hits: { type: 'array', items: MEMORY_ITEM_SCHEMA },
          coldHits: { type: 'array', items: MEMORY_ITEM_SCHEMA },
          gap: { type: 'boolean' },
          total: { type: 'integer' },
        },
        required: ['text'],
      })),
      async execute(args, exec) {
        const query = String(args?.query ?? '').trim()
        if (!query) throw new Error('query 不能为空')
        const topK = Math.max(1, Math.min(Number(args?.topK ?? cfg.maxResults) || cfg.maxResults, 20))
        const { stdout } = await runCommand(cfg, ['search', query, '--json', '--top-k', String(topK)], exec.signal)
        const data = parseJson(stdout)
        const hits = (Array.isArray(data.units) ? data.units : []).map(toHit)
        const coldHits = (Array.isArray(data.cold_units) ? data.cold_units : []).map(toHit)
        const lines = []
        if (hits.length === 0 && coldHits.length === 0) {
          lines.push(`查询「${query}」：记忆库没有相关经验。`)
          lines.push('结论：明确告诉用户“记忆里没有相关经验”，按通用方法处理，不要编造过往经历。')
        } else {
          lines.push(`查询「${query}」：热区命中 ${hits.length} 条${hits.length >= topK ? '（仅显示前 topK 条，可调大 topK）' : ''}`)
          lines.push(hits.map((hit, index) => hitLines(hit, index + 1)).join('\n'))
          if (coldHits.length > 0) {
            lines.push(`\n冷库（镜像只读，不改状态）命中 ${coldHits.length} 条：`)
            lines.push(coldHits.map((hit, index) => hitLines(hit, index + 1)).join('\n'))
          }
          lines.push('\n用法：先看 premise 是否匹配当前场景；命中 counter 就不要硬套；冲突成对呈现；'
            + 'stale 的经验提示可能过时；需要原文细节时用 memory_get。')
        }
        return {
          text: lines.join('\n'),
          hits,
          coldHits,
          gap: data.gap === true,
          total: hits.length + coldHits.length,
        }
      },
      presentCall: (args) => ({ card: 'generic', title: `记忆检索：${clip(args?.query, 40)}`, kind: 'search' }),
    },
    {
      name: 'memory_get',
      description: '按 id 读取一条经验的完整内容（原文 detail + 全部元数据）。'
        + '当检索结果需要细节、或用户问“当时具体怎么做的/出处是哪”时调用。',
      parameters: {
        id: { type: 'string', required: true, description: '记忆单元 id，形如 u_1a2b3c4d5e6f（来自 memory_search 结果）。' },
      },
      output: textOutput(() => ({
        type: 'object',
        additionalProperties: false,
        properties: {
          text: { type: 'string' },
          id: { type: 'string' },
          summary: { type: 'string' },
          detail: { type: 'string' },
          premise: { type: 'string' },
          counter: { type: 'string' },
          maturity: { type: 'string' },
          confidence: { type: 'number' },
          sourceRef: { type: 'string' },
          truncated: { type: 'boolean' },
        },
        required: ['text', 'id'],
      })),
      async execute(args, exec) {
        const id = String(args?.id ?? '').trim()
        if (!id) throw new Error('id 不能为空')
        const { stdout } = await runCommand(cfg, ['export', '--id', id], exec.signal)
        const unit = parseJson(stdout)
        const detail = String(unit.content ?? '')
        const truncated = detail.length > DETAIL_MAX
        const shown = truncated ? `${detail.slice(0, DETAIL_MAX)}…(已截断，共 ${detail.length} 字)` : detail
        const lines = [
          `${unit.unit_id} · ${unit.maturity} · 可信 ${unit.score} · ${unit.case_type}${unit.pinned ? ' · 🔒重要' : ''}`,
          `摘要: ${unit.summary}`,
        ]
        if (unit.premise) lines.push(`前提: ${unit.premise}`)
        if (unit.counter) lines.push(`坑/限: ${unit.counter}`)
        if (unit.source_ref) lines.push(`溯源: ${unit.source_ref}`)
        lines.push(`创建: ${unit.timestamp} · 版本 v${unit.version} · 解封计数 ${unit.access_unseal_count}`)
        if (shown) lines.push(`原文:\n${shown}`)
        return {
          text: lines.join('\n'),
          id: unit.unit_id,
          summary: String(unit.summary ?? ''),
          detail: shown,
          premise: String(unit.premise ?? ''),
          counter: String(unit.counter ?? ''),
          maturity: String(unit.maturity ?? ''),
          confidence: Number(unit.score ?? 0),
          sourceRef: String(unit.source_ref ?? ''),
          truncated,
        }
      },
      presentCall: (args) => ({ card: 'generic', title: `读取经验 ${clip(args?.id, 24)}`, kind: 'read' }),
    },
    {
      name: 'memory_status',
      description: '记忆库概览：经验总数、冷库归档、待批提案、成熟度分布、最近经验与数据位置。'
        + '用于回答“你记得多少/记忆库在哪/有没有待审批的东西”。',
      parameters: {},
      output: textOutput(() => ({
        type: 'object',
        additionalProperties: false,
        properties: { text: { type: 'string' }, dataDir: { type: 'string' } },
        required: ['text'],
      })),
      async execute(_args, exec) {
        const [status, audit] = await Promise.all([
          runCommand(cfg, ['status'], exec.signal),
          runCommand(cfg, ['audit'], exec.signal),
        ])
        const text = `${status.stdout.trim()}\n\n${audit.stdout.trim()}`
        return { text, dataDir: resolveDataDir(cfg) }
      },
      presentCall: () => ({ card: 'generic', title: '记忆库概览', kind: 'read' }),
    },
    {
      name: 'memory_save',
      description: '把一条经验写入长期记忆库，下次任务可用 memory_search 检索到。'
        + '只在用户明确同意后调用（先用一两句话向用户确认要记什么），不要自动记录闲聊或隐私信息。'
        + '写入前会请求用户批准。',
      parameters: {
        summary: {
          type: 'string',
          required: true,
          description: '一句话经验摘要（≤200 字），写清“做了什么 + 结论/坑 + 下次怎么做”。',
        },
        detail: { type: 'string', description: '原文/细节，可空。长内容放这里，摘要保持一句话。' },
        tags: { type: 'string', description: '2~4 个逗号分隔的标签，如 “闲鱼,定价”。' },
        premise: { type: 'string', description: '适用前提：什么时候这条经验才成立。' },
        counter: { type: 'string', description: '反面/坑：什么情况下不能用这条经验。' },
        score: { type: 'number', description: '可信度 0~1，默认 0.6；有实据的填 0.8 以上。' },
        caseType: {
          type: 'string',
          enum: ['success', 'fail', 'neutral'],
          description: 'success 成功经验 / fail 踩坑教训 / neutral 中性记录（默认）。',
        },
        pinned: { type: 'boolean', description: '标记为重要经验：不参与自动冷封存，删除会被拦截。' },
        sourceRef: { type: 'string', description: '溯源：这条经验来自哪个对话/任务/文档。' },
      },
      output: textOutput(() => ({
        type: 'object',
        additionalProperties: false,
        properties: {
          text: { type: 'string' },
          id: { type: 'string' },
          summary: { type: 'string' },
          maturity: { type: 'string' },
        },
        required: ['text'],
      })),
      async execute(args, exec) {
        requireWrites(cfg)
        const summary = String(args?.summary ?? '').trim()
        if (!summary) throw new Error('summary 不能为空')
        const caseType = ['success', 'fail', 'neutral'].includes(args?.caseType) ? args.caseType : 'neutral'
        const score = Number.isFinite(args?.score) ? Math.max(0, Math.min(1, args.score)) : 0.6
        await ensureApproval(ctx, exec, cfg, clip(summary, 40))

        const argv = ['remember', summary]
        if (args?.detail) argv.push('--detail', String(args.detail))
        if (args?.tags) argv.push('--tags', String(args.tags))
        if (args?.premise) argv.push('--premise', String(args.premise))
        if (args?.counter) argv.push('--counter', String(args.counter))
        argv.push('--score', String(score), '--case-type', caseType)
        if (args?.pinned === true) argv.push('--pin')
        if (args?.sourceRef) argv.push('--source-ref', String(args.sourceRef))

        const { stdout } = await runCommand(cfg, argv, exec.signal)
        const matched = /(u_[0-9a-f]{12})/.exec(stdout)
        const id = matched ? matched[1] : ''
        return {
          text: [`已写入经验${id ? ` ${id}` : ''}（待验证 draft，经 feedback 验证后升级）`, `摘要: ${clip(summary, SUMMARY_MAX)}`]
            .concat(args?.premise ? [`前提: ${clip(args.premise, FIELD_MAX)}`] : [])
            .concat(args?.counter ? [`坑/限: ${clip(args.counter, FIELD_MAX)}`] : [])
            .join('\n'),
          id,
          summary: clip(summary, SUMMARY_MAX),
          maturity: 'draft',
        }
      },
      presentCall: (args) => ({ card: 'generic', title: `记住：${clip(args?.summary, 40)}`, kind: 'edit', rawInput: { summary: args?.summary } }),
    },
    {
      name: 'memory_feedback',
      description: '回写一条经验的实际效果：good 验证有效 / bad 翻车 / neutral 无明显效果。'
        + '用户按某条经验办完事后调用，用于调整可信度与成熟度（good 推进升级，bad 置为已翻车但保留提醒）。',
      parameters: {
        id: { type: 'string', required: true, description: '记忆单元 id（来自 memory_search 结果）。' },
        result: {
          type: 'string',
          enum: ['good', 'bad', 'neutral'],
          required: true,
          description: '实际效果：good 有效 / bad 翻车 / neutral 无明显效果。',
        },
        note: { type: 'string', description: '一句话说明本次效果（可选）。' },
      },
      output: textOutput(() => ({
        type: 'object',
        additionalProperties: false,
        properties: { text: { type: 'string' }, id: { type: 'string' }, maturity: { type: 'string' } },
        required: ['text'],
      })),
      async execute(args, exec) {
        requireWrites(cfg)
        const id = String(args?.id ?? '').trim()
        const result = String(args?.result ?? '')
        if (!id) throw new Error('id 不能为空')
        if (!['good', 'bad', 'neutral'].includes(result)) throw new Error('result 只能是 good / bad / neutral')
        await ensureApproval(ctx, exec, cfg, `${result} · ${id}`)
        const argv = ['feedback', '--id', id, '--result', result]
        if (args?.note) argv.push('--note', String(args.note))
        const { stdout } = await runCommand(cfg, argv, exec.signal)
        return { text: clip(stdout, 1200), id, maturity: '' }
      },
      presentCall: (args) => ({ card: 'generic', title: `效果回写 ${clip(args?.result, 12)}`, kind: 'other' }),
    },
  ]

  for (const tool of tools) {
    ctx.effect(() => ctx.tools.register(tool))
  }

  if (cfg.promptHint) {
    const systemPrompt = ctx.get('systemPrompt')
    if (systemPrompt) {
      ctx.effect(() => systemPrompt.section({
        name: 'dsh-memory',
        order: 88,
        text: PROMPT_HINT,
      }))
    }
  }

  // 前端星图的数据口：同源只读 JSON，不暴露任何写能力。
  ctx.inject(['webServer'], (webCtx) => {
    webCtx.effect(() => webCtx.webServer.register({
      kind: 'exact',
      path: STAR_MAP_PATH,
      handler: async (_req, res) => {
        const reply = (body) => {
          const payload = Buffer.from(JSON.stringify(body), 'utf-8')
          res.writeHead(200, {
            'Content-Type': 'application/json; charset=utf-8',
            'Content-Length': payload.length,
            'Cache-Control': 'no-store',
          })
          res.end(payload)
        }
        try {
          reply(await loadStarmap(cfg))
        } catch (error) {
          reply({
            ok: false,
            error: String(error?.message ?? error),
            dataDir: resolveDataDir(cfg),
            zones: [], subzones: [], units: [], cold: [], mirrors: [],
            stats: { zones: 0, subzones: 0, units: 0, cold: 0, pending: 0, pinned: 0 },
          })
        }
      },
    }))

    // 星图上的「批准/驳回」按钮：人点了才算数，所以这是唯一一条写路由。
    // 只接受 pending 提案的 approve --execute / reject，且可通过 allowUiWrites 关掉。
    webCtx.effect(() => webCtx.webServer.register({
      kind: 'exact',
      path: PROPOSAL_ACTION_PATH,
      handler: async (req, res) => {
        const reply = (status, body) => {
          const payload = Buffer.from(JSON.stringify(body), 'utf-8')
          res.writeHead(status, {
            'Content-Type': 'application/json; charset=utf-8',
            'Content-Length': payload.length,
            'Cache-Control': 'no-store',
          })
          res.end(payload)
        }
        if (req.method !== 'POST') {
          reply(405, { ok: false, error: '只接受 POST' })
          return
        }
        if (cfg.allowUiWrites === false) {
          reply(403, { ok: false, error: '插件配置 allowUiWrites=false，已禁用界面操作' })
          return
        }
        try {
          const chunks = []
          for await (const chunk of req) chunks.push(chunk)
          const raw = Buffer.concat(chunks).toString('utf-8')
          const body = raw ? JSON.parse(raw) : {}
          const id = String(body.id || '').trim()
          const action = String(body.action || '').trim()
          if (!/^p_[0-9a-f]{12}$/.test(id)) {
            reply(400, { ok: false, error: '提案 id 不合法' })
            return
          }
          let argv
          if (action === 'approve') argv = ['approve', id, '--execute']
          else if (action === 'reject') argv = ['reject', id, '-r', '界面驳回']
          else {
            reply(400, { ok: false, error: 'action 只能是 approve / reject' })
            return
          }
          const { stdout } = await runCommand(cfg, argv)
          reply(200, { ok: true, message: stdout.trim() })
        } catch (error) {
          reply(200, { ok: false, error: String(error?.message ?? error) })
        }
      },
    }))
  })
}
