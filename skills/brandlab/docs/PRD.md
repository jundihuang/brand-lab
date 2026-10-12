# BrandLab Translation Skill PRD

- Version: v0.2
- Date: 2026-10-12
- Status: Draft
- Product Type: Codex-native Skill
- Directory: `~/beansmile/codes/skills/brandlab`

## 1. Summary

BrandLab Translation Skill 是一个面向 Codex 的文件型品牌翻译治理 Skill。它用本地文件管理品牌资料、翻译规则、术语表、内容目录、翻译任务、校验结果、人工审批和导出产物。

产品只有两种运行模式：

| Mode | 名称 | 定位 |
|---|---|---|
| Mode 1 | Pure Skill Mode | 默认模式，只处理本地文件，不连接 Shopify。 |
| Mode 3 | Shopify Controlled Execution Mode | 可选模式，通过 Shopify AI Toolkit + Shopify CLI 做受控读取和受控写入。 |

不提供独立的 Read Mode。Shopify 读取只作为 Controlled Execution 前置步骤存在，最终必须服务于翻译、审批或受控写入。

## 2. Problem

品牌内容翻译常见问题：

1. 翻译结果不稳定，品牌语气和术语难以保持一致。
2. 占位符、HTML、SEO 长度、禁用词、法务表述容易被破坏。
3. 导入内容可能不是项目主语言，系统不能错误假设 source language。
4. 翻译结果缺少可复现的质量审计。
5. 团队需要可审查、可导出的文件，而不是一个额外平台。

BrandLab 通过 guideline-driven translation、deterministic validation、human approval 和 file-based audit 解决这些问题。

## 3. Goals

### Product Goals

1. 提供无后端、无数据库的 Codex-native localization workflow。
2. 强制应用品牌规则、术语规则和确定性校验。
3. 支持非主语言内容导入和翻译。
4. 每个翻译项都有审计结果和 Trust Score。
5. 人工审批后才允许导出或写入 Shopify。
6. 所有状态保存在透明、可版本化的本地文件中。
7. 提供清晰的安装引导、模式选择引导和每一步的 next action。

### Non-goals

1. 多人管理后台。
2. Role / permission 系统。
3. 社交媒体内容生成。
4. 社交媒体平台 API 或发布。
5. 图片生成、图片编辑、图片任务执行、图片提示词生成。
6. 专家工作台。
7. 自定义 Shopify App。
8. Shopify OAuth token 管理。
9. Background job queue。
10. Cloud database。

## 4. Target Users

### Primary user

**Localization operator**

负责准备翻译内容的人。希望得到稳定输出、可见校验结果和明确的下一步操作。

### Secondary user

**Brand owner / reviewer**

审查翻译是否保持品牌语气、术语、法务约束和格式的人。

### Technical user

**Developer / agency implementer**

安装 Skill、维护规则文件、配置 Shopify 连接或导出格式的人。

## 5. Product Principles

1. **File-first**  
   配置、源内容、翻译、审计、审批和导出记录都保存在本地文件中。

2. **Guideline-driven**  
   翻译必须使用显式 guideline，而不是只依赖模型默认行为。

3. **Deterministic validation**  
   硬规则由脚本校验，不靠模型自行判断。

4. **Human approval required**  
   内容不能自动变成 approved。导出和 Shopify 写入都必须基于明确审批。

5. **Safe Shopify execution**  
   默认是 Pure Skill Mode。Shopify Controlled Execution Mode 必须经过安装、认证、计划生成和人工确认后才执行。

6. **Guided workflow**  
   每个功能都有 Trigger、Input、Workflow、Output、Guardrails 和 Next Action。Skill 不能只输出结果，还必须告诉用户下一步做什么。

## 6. Runtime Modes

### Mode 1: Pure Skill Mode

默认模式。

适用场景：

1. 用户已有 CSV、JSON、JSONL、Markdown、HTML 或纯文本内容。
2. 用户不需要 BrandLab 直接连 Shopify。
3. 用户希望先本地翻译、审查、导出，再自行导入 Shopify。

Boundary：

1. 只读写项目内 `brandlab/` workspace。
2. 不访问 Shopify。
3. 不要求安装 Shopify CLI 或 Shopify AI Toolkit。
4. 输出 Shopify-compatible CSV/JSON 是允许的。

### Mode 3: Shopify Controlled Execution Mode

可选模式。

适用场景：

1. 用户希望 BrandLab 通过 Shopify CLI 读取店铺内容。
2. 用户希望将 approved 翻译写入 Shopify。
3. 用户接受显式审批和分批执行。

Boundary：

1. 必须先完成 Shopify AI Toolkit + Shopify CLI 安装引导。
2. 认证由 Shopify CLI 管理，BrandLab 不保存 token。
3. 读取内容用于生成 content catalog、翻译计划和写入计划。
4. 写入必须先生成 mutation plan，再获得人工确认。
5. 默认 `allow_mutations: false`。
6. 每次 mutation batch 最大 10 个 item。
7. 每次执行结果写入本地 history。

## 7. Core Concepts

### Brand Profile

品牌基础配置。

```yaml
brand: Misssixty
mode: pure
source_language: en
working_language: zh-CN
default_tone: premium, confident, minimal

markets:
  - market: IT
    locale: it-IT
    enabled: true
  - market: FR
    locale: fr-FR
    enabled: false
```

Mode 3 配置示例：

```yaml
brand: Misssixty
mode: shopify_controlled
source_language: en
working_language: zh-CN

shopify:
  enabled: true
  store: misssixty-en
  allow_reads: true
  allow_mutations: false
  max_batch_size: 10
```

### Guideline

按 resource type 和 target locale 生效的规则包。

```yaml
id: product-title-it
name: Product Title — Italian
resource_type: product_title
target_locale: it-IT
status: active
version: 1

rules:
  - type: instruction
    rule: Keep the brand name unchanged.
  - type: max_length
    value: 70
  - type: preserve
    value: placeholders
  - type: forbidden_regex
    pattern: "(?i)\\b(saldi|sconto)\\b"
```

### Glossary

本地术语表。

```csv
source_term,target_term,target_locale,status,note
Miss Sixty,Miss Sixty,it-IT,locked,Brand name
Cashmere,Cashmere,it-IT,locked,Material term
```

### Content Catalog

规范化后的源内容列表。

```json
{
  "id": "product_1001",
  "resource_type": "product_title",
  "source_locale": "en",
  "target_locale": "it-IT",
  "source": "Leopard-print mini skirt",
  "digest": "sha256:...",
  "status": "pending"
}
```

### Translation Job

一个本地任务目录，包含 source snapshot、translation、audit、approval state 和 export input。

### Mutation Plan

Mode 3 专有对象。写入 Shopify 前必须生成，包含目标资源、GraphQL 操作、before/after、source digest、audit 摘要和 approval 状态。

### Export Manifest

导出记录，包含 locale、item count、source digest、format 和输出路径。

## 8. Workspace Structure

```text
your-project/
└── brandlab/
    ├── brand.yaml
    ├── guidelines/
    ├── glossary/
    ├── inbox/
    ├── content/
    │   └── catalog.jsonl
    ├── jobs/
    ├── approvals/
    ├── exports/
    ├── shopify/
    │   ├── setup-report.md
    │   ├── discovery/
    │   ├── mutation-plans/
    │   └── execution-results/
    ├── reports/
    └── history/
        └── events.jsonl
```

`shopify/` 只在 Mode 3 使用。Pure Skill Mode 可以不创建该目录。

## 9. Skill Package Structure

```text
~/beansmile/codes/skills/brandlab/
├── SKILL.md
├── README.md
├── docs/
│   └── PRD.md
├── schemas/
├── rules/
├── scripts/
├── templates/
└── examples/
```

Priority files:

1. `SKILL.md`
2. `README.md`
3. `docs/PRD.md`
4. `schemas/*.json`
5. `templates/*.yaml` and `templates/*.md`
6. `scripts/*.py`
7. `examples/`

## 10. Onboarding And Installation

### Setup Interview

Skill 初始化时必须先进入 setup interview：

1. 询问使用 Pure Skill Mode 还是 Shopify Controlled Execution Mode。
2. 询问品牌名、source language、working language 和目标市场。
3. 询问是否要生成示例 guideline 和术语表。
4. 输出 `brandlab/setup-report.md`。
5. 明确告诉用户下一步命令。

### Mode 1 setup

Required checks:

1. 项目目录可写。
2. Python 3.11+ 可用。
3. `brand.yaml` 可创建或可读取。
4. 目标目录没有冲突文件。

Skill guidance:

```text
将使用 Pure Skill Mode。
BrandLab 只会读写 ./brandlab 下的文件。
下一步：把源文件放到 brandlab/inbox/，然后执行：
Import files in brandlab/inbox into the BrandLab catalog.
```

### Mode 3 setup

Mode 3 依赖两个官方工具：

1. Shopify AI Toolkit。
2. Shopify CLI。

Required checks:

1. Node.js 18+。
2. Shopify CLI 可用。
3. Shopify AI Toolkit 可用。
4. Shopify CLI 已完成 store authentication。
5. `brand.yaml.shopify.allow_mutations` 默认为 `false`。

Installation guidance:

```bash
# Check Node.js
node --version

# Install Shopify CLI
npm install -g @shopify/cli@latest

# Add Shopify AI Toolkit to Codex
codex plugin add shopify@openai-curated

# Authenticate the store through Shopify CLI
shopify store auth

# Verify available stores
shopify store list

# Verify the selected store
shopify store info
```

Skill rules:

1. Skill 必须先检测命令是否存在，再决定是否提示安装。
2. 安装和认证命令必须由用户确认后才能执行。
3. 如果安装失败，Skill 必须输出错误含义、可能原因和修复步骤。
4. 如果认证失败，Skill 必须引导用户重新运行 Shopify CLI 认证。
5. BrandLab 不读取、复制、保存或展示 Shopify token。
6. 首次进入 Mode 3 时，只允许做 setup check 和只读 discovery。
7. `allow_mutations` 不能由 Skill 自动改成 `true`，必须由用户显式要求并确认。

### Setup report

每次 setup 必须生成 `brandlab/setup-report.md`，包含：

1. Selected mode。
2. Workspace path。
3. Python / Node / Shopify CLI / AI Toolkit 检查结果。
4. Store identifier，仅当 Mode 3 已配置。
5. 当前 `allow_reads` / `allow_mutations` 状态。
6. 已创建文件。
7. 已跳过文件。
8. Next action。

## 11. Functional Requirements

每个功能必须满足同一 workflow contract：

1. Trigger：用户怎么说或怎么做。
2. Input：读取哪些文件。
3. Workflow：按顺序执行哪些步骤。
4. Output：生成或更新哪些文件。
5. Guidance：完成后给用户什么下一步。
6. Guardrails：什么情况下停止或要求确认。
7. Acceptance：怎样算通过。

### FR-01 Initialize Workspace

Trigger examples:

```text
Initialize BrandLab for this project.
```

Workflow:

1. If `brandlab/brand.yaml` exists, load it and ask whether to reuse.
2. If missing, run setup interview.
3. Create required directories.
4. Create `brand.yaml`.
5. Create default guideline templates.
6. Create glossary template.
7. Create empty catalog and history.
8. Generate setup report.
9. Output next action.

Output:

1. `brandlab/brand.yaml`
2. `brandlab/guidelines/`
3. `brandlab/glossary/glossary.csv`
4. `brandlab/content/catalog.jsonl`
5. `brandlab/inbox/`
6. `brandlab/jobs/`
7. `brandlab/approvals/`
8. `brandlab/exports/`
9. `brandlab/reports/`
10. `brandlab/history/events.jsonl`
11. `brandlab/setup-report.md`

Guardrails:

1. 重复初始化不能覆盖用户文件。
2. 无效配置必须停止并指出字段。
3. Mode 3 未完成 setup check 时，不能启用 Shopify connector。

Acceptance:

1. 第二次初始化不会覆盖已有内容。
2. 缺失目录会被补齐。
3. 用户能从输出中知道下一步。

### FR-02 Manage Brand Profile

Workflow:

1. Read `brand.yaml`.
2. Validate required fields.
3. Check enabled markets.
4. Check mode-specific settings.
5. Report invalid or risky fields.

Required fields:

1. Brand name.
2. Mode.
3. Source language.
4. Working language.
5. Default tone.
6. Enabled markets and locales.

Mode 3 additional fields:

1. `shopify.enabled`
2. `shopify.store`
3. `shopify.allow_reads`
4. `shopify.allow_mutations`
5. `shopify.max_batch_size`

Acceptance:

1. Disabled markets 不进入默认任务。
2. Missing source language 阻止翻译。
3. Mode 3 缺少 store 配置时不能执行 Shopify discovery。
4. `allow_mutations: true` 必须有明确变更记录。

### FR-03 Manage Guidelines

Workflow:

1. List `guidelines/`。
2. Parse YAML or Markdown front matter。
3. Validate schema。
4. Match resource type、market、locale 和 status。
5. 在翻译前 merge 匹配 guidelines。
6. 将 guideline version 写入 job、translation、audit 和 export manifest。

Required rule types:

1. Instruction。
2. Locked term。
3. Forbidden term。
4. Forbidden regex。
5. Required regex。
6. Max length。
7. Min length。
8. Placeholder preservation。
9. HTML tag preservation。
10. Legal or compliance claim preservation。

Acceptance:

1. Inactive guideline 不生效。
2. Guideline parse error 阻止翻译。
3. 相同 scope 的规则冲突必须在翻译前暴露。
4. Audit result 能追溯到 guideline version。

### FR-04 Manage Glossary

Workflow:

1. Read `glossary/*.csv`。
2. Validate locale 和 status。
3. Build per-job glossary snapshot。
4. Inject locked / preferred / forbidden terms。
5. Deterministic validator 检查翻译结果。

Statuses:

1. `locked`
2. `preferred`
3. `forbidden`
4. `draft`

Acceptance:

1. `locked` term 不一致时阻止 approval。
2. `forbidden` term 阻止 approval。
3. `preferred` term 缺失时产生 warning。
4. `draft` term 不进入硬规则。

### FR-05 Import Source Content

Trigger examples:

```text
Import files in brandlab/inbox into the BrandLab catalog.
```

Workflow:

1. Scan `brandlab/inbox/`。
2. Detect file format。
3. Ask user to confirm source locale if file metadata 不明确。
4. Normalize resource type。
5. Generate stable item ID。
6. Calculate source digest。
7. Compare with existing catalog。
8. Mark item as `new`, `changed`, or `unchanged`。
9. Update `content/catalog.jsonl`。
10. Output import report 和 next action。

Supported input:

1. CSV。
2. JSON。
3. JSONL。
4. Markdown。
5. Plain text。
6. HTML。

Common Shopify-style fields:

1. Handle。
2. Title。
3. Body HTML。
4. SEO Title。
5. SEO Description。
6. Collection title。
7. Collection description。

Acceptance:

1. 同一内容重复导入不会产生重复 ID。
2. 内容变化会标记为 `changed`。
3. 关联翻译会标记为 `stale`。
4. Import 不假设内容一定是项目主语言。

### FR-06 Existing Content Audit

Workflow:

1. Select catalog items。
2. Match active guidelines。
3. Load glossary。
4. Run deterministic checks。
5. Generate machine-readable audit。
6. Generate human-readable report。
7. Suggest translation、correction 或 ignore。

Checks:

1. Missing translation。
2. Empty content。
3. Suspected untranslated source text。
4. Language mismatch。
5. Forbidden terms。
6. Required terms。
7. Placeholder issues。
8. HTML structure issues。
9. SEO length issues。
10. Repeated template patterns。
11. Invalid legal claims。
12. Glossary inconsistency。

Acceptance:

1. Audit 不修改源内容。
2. 每个 item 都有 machine-readable status。
3. Report 能解释每类问题数量。
4. 用户能根据 report 创建翻译任务。

### FR-07 Create Translation Job

Workflow:

1. Ask target locale / market。
2. Filter by resource type、status、IDs、source file 或 guideline。
3. Snapshot selected source content。
4. Snapshot guidelines and glossary。
5. Create job manifest。
6. Set job status to `ready`。
7. Tell user whether to translate now or review job first。

Filters:

1. Locale。
2. Market。
3. Resource type。
4. Status。
5. Content IDs。
6. Source file。
7. Guideline ID。

Acceptance:

1. Job ID 唯一。
2. Source digest 保存到 job。
3. Job manifest 能解释任务范围。
4. 任务创建后不会隐式修改源文件。

### FR-08 Translate Content

Workflow:

1. Load job manifest。
2. Confirm job status 是 `ready`。
3. Load brand profile。
4. Load active guidelines。
5. Load glossary snapshot。
6. Detect source locale。
7. Translate each item。
8. Save translation file。
9. Record source digest、guideline versions 和 glossary digest。
10. Run deterministic validation。
11. Update job and item status。
12. Generate next-action summary。

Supported resource types:

1. Product title。
2. Product description。
3. Product variant。
4. Collection title。
5. Collection description。
6. Page title。
7. Page body。
8. Blog title。
9. Blog body。
10. SEO title。
11. SEO description。
12. Care and compliance text。

Acceptance:

1. Translation 不修改 source snapshot。
2. Every translation records source digest。
3. Every translation records guideline and glossary versions。
4. Validation failure 会阻止 approved。

### FR-09 Non-Primary-Language Import

Workflow:

1. Ask user to declare source locale。
2. Ask target locale。
3. Ask optional bridge locale。
4. Validate locale values。
5. Run language suspicion check。
6. Translate directly or through bridge locale。
7. Clearly label bridge output。
8. Add language mismatch warnings to audit。

Example:

```yaml
source_locale: zh-CN
target_locale: it-IT
bridge_locale: en
```

Acceptance:

1. Skill 不假设 imported content 一定是 store primary language。
2. Bridge output 可选且必须标记。
3. Language mismatch 进入 audit。
4. Translation output 记录实际 source locale。

### FR-10 Deterministic Validation

Workflow:

1. Read source snapshot。
2. Read translation。
3. Read guideline rules。
4. Read glossary rules。
5. Run script-based validators。
6. Build audit JSON。
7. Update item status。
8. Summarize hard failures、soft warnings 和 passed checks。

Validators:

1. Placeholder parity。
2. HTML tag parity。
3. Locked glossary enforcement。
4. Forbidden term detection。
5. Forbidden regex detection。
6. Required regex detection。
7. Min and max length。
8. Empty translation detection。
9. Untranslated source detection。
10. Duplicate output detection。
11. Source digest consistency。
12. Target locale consistency。

Acceptance:

1. Hard-rule failure 阻止 approval。
2. Soft-rule warning 进入 review。
3. Validator 可离线重复执行。
4. Result includes rule ID、status、severity、message。

### FR-11 Trust Score

Workflow:

1. Read audit result。
2. Apply deterministic scoring。
3. Store score in audit JSON。
4. Show score in review file。
5. Use score to classify next action。

Suggested scoring:

| Score | Meaning |
|---|---|
| `100` | Hard rules passed, no warnings |
| `90-99` | Hard rules passed, minor warnings |
| `70-89` | Requires human review |
| `0-69` | Hard-rule failure |

Acceptance:

1. Score reproducible from audit。
2. Hard failure cannot be approved。
3. Review file explains score reason。

### FR-12 Generate Review File

Workflow:

1. Read job manifest。
2. Read translations。
3. Read audits。
4. Aggregate counts。
5. Generate `review.md`。
6. Add per-item source、translation、checks、score 和 suggested decision。
7. Output review path 和 approval guidance。

Review file includes:

1. Job summary。
2. Total item count。
3. Passed / warning / failed / blocked counts。
4. Source text。
5. Translated text。
6. Guideline IDs。
7. Glossary references。
8. Validation results。
9. Trust Score。
10. Suggested decision。

Acceptance:

1. 不需要外部 UI 即可审查。
2. 审查者能理解每项通过或失败原因。
3. 同一 job state 生成相同 review 内容。

### FR-13 Human Approval

Workflow:

1. Read review file or job manifest。
2. Ask user to approve、reject 或 request edit。
3. Validate hard rules。
4. Update approval file。
5. Record reviewer、timestamp、decision 和 source digest。
6. Update job status。
7. Tell user whether ready for export or Shopify mutation。

Item statuses:

1. `pending`
2. `translated`
3. `needs_review`
4. `approved`
5. `rejected`
6. `blocked`
7. `exported`
8. `stale`

Acceptance:

1. Hard-rule failure cannot be approved。
2. Approval records source digest。
3. Source digest change invalidates prior approval。
4. Rejected content is excluded from default export。
5. Bulk approval 必须显示影响范围。

### FR-14 Export Deliverables

Workflow:

1. Select job or approved items。
2. Confirm export format。
3. Filter approved items。
4. Map fields to target format。
5. Build output file。
6. Build export manifest。
7. Mark exported items。
8. Output export path、item count 和 next action。

Formats:

1. CSV。
2. JSON。
3. JSONL。
4. Markdown。
5. HTML-safe text。
6. Shopify-compatible CSV。

Acceptance:

1. Default export only approved items。
2. Export manifest records locale、count、time、digest 和 format。
3. Existing exports are not overwritten automatically。
4. Partially approved job can export approved subset。

### FR-15 Job History

Workflow:

1. Append event after every state-changing operation。
2. Include timestamp、actor、event type、object ID 和 payload reference。
3. Never rewrite old events。
4. Support human-readable job summary。

Events:

1. Workspace initialized。
2. Setup completed。
3. Content imported。
4. Guideline changed。
5. Glossary changed。
6. Job created。
7. Translation generated。
8. Validation completed。
9. Item approved。
10. Item rejected。
11. Export created。
12. Shopify mutation planned。
13. Shopify mutation approved。
14. Shopify mutation executed。
15. Shopify execution failed。

Acceptance:

1. History is append-only。
2. Every event has payload reference。
3. History does not replace Git history。

### FR-16 Shopify Controlled Execution

仅 Mode 3 可用。

#### Setup workflow

1. Check Node.js。
2. Check Shopify CLI。
3. Check Shopify AI Toolkit。
4. Guide installation if missing。
5. Run Shopify CLI authentication。
6. Verify selected store。
7. Set `shopify.enabled: true`。
8. Keep `allow_mutations: false`。
9. Generate setup report。
10. Ask user whether to run read-only discovery。

#### Discovery workflow

1. Confirm `shopify.enabled: true`。
2. Confirm `allow_reads: true`。
3. Use AI Toolkit / local schema knowledge to validate intended GraphQL。
4. Use Shopify CLI to execute read-only query。
5. Normalize supported translatable fields。
6. Write snapshot to `shopify/discovery/`。
7. Import normalized snapshot into content catalog。
8. Output catalog path 和 next action。

Discovery may read only what is needed for translation planning:

1. Product title。
2. Product description。
3. Supported SEO fields。
4. Collection title / description。
5. Page title / body。
6. Blog title / body。
7. Market and locale availability。

#### Translation workflow

1. Use discovery snapshot as source content。
2. Continue FR-07 到 FR-13。
3. 人工审批仍然必须执行。
4. Discovery snapshot 不等于 approved translation。

#### Mutation planning workflow

1. Select approved items。
2. Confirm target Shopify resource IDs。
3. Re-check source digest。
4. Build before/after values。
5. Generate GraphQL mutation draft。
6. Validate operation shape using Shopify AI Toolkit。
7. Generate `shopify/mutation-plans/<batch-id>.json`。
8. Generate human-readable mutation preview。
9. Show affected resources、fields、before/after、risk 和 rollback source。
10. Require explicit user confirmation。

#### Mutation execution workflow

1. Confirm user explicitly asked to execute。
2. Confirm plan status 是 `approved`。
3. Confirm batch size <= `max_batch_size`。
4. Confirm source digest and approval digest 仍然一致。
5. Verify exact Shopify CLI command flags using local CLI help。
6. Execute one batch。
7. Save raw CLI output to `shopify/execution-results/`。
8. Parse success / failure count。
9. Update item status。
10. Append history event。
11. Report next batch or stop condition。

Execution guardrails:

1. 默认不写入。
2. 没有显式 approval 不能执行 mutation。
3. Single batch maximum 10 items。
4. 失败 batch 必须停止，不自动重试。
5. 删除类操作不在 MVP 范围。
6. Unsupported resource type 不执行。
7. CLI flag 不确定时必须先运行 help 或查询官方文档，不能猜测。

#### Verification workflow

1. Read execution result。
2. Confirm Shopify response indicates success。
3. For high-risk updates, run read-back query when schema supports it。
4. Compare returned values with approved after values。
5. Mark item as `verified` or `execution_mismatch`。
6. Append verification event。

## 12. Example: Add Italian Content For An English Store

### Mode 1 flow

```text
User: 用 BrandLab 初始化这个项目。
Skill: 选择 Pure Skill Mode，创建 workspace，提示放入源文件。

User: 把英文商品导出 CSV 放进 inbox。
Skill: Import files in brandlab/inbox into the BrandLab catalog.

Skill: 生成 product title / description / SEO catalog items。

User: 为 it-IT 创建翻译任务。
Skill: 创建 job。

User: Translate this job.
Skill: 翻译、校验、生成 review.md。

User: Approve items that passed all hard rules.
Skill: 记录 approval。

User: Export approved it-IT items as Shopify-compatible CSV.
Skill: 生成 CSV 和 manifest。
```

### Mode 3 flow

```text
User: 使用 Shopify Controlled Execution Mode。
Skill: 检查 Node、Shopify CLI、AI Toolkit。
Skill: 引导 shopify store auth。
Skill: 验证 store 和意大利市场语言状态。

User: Import product content from Shopify for translation.
Skill: 只读 discovery，生成 local catalog。

User: Create an it-IT translation job.
Skill: 创建 job。

User: Translate this job.
Skill: 翻译、校验、生成 review.md。

User: Approve items that passed all hard rules.
Skill: 记录 approval。

User: Prepare Shopify mutations.
Skill: 生成 mutation plan 和 before/after preview。

User: Approve batch BATCH-001 and prepare execution.
Skill: 将 plan 标记为 approved，保持等待执行。

User: Execute batch BATCH-001.
Skill: 校验审批和 digest，执行一批，保存结果，报告成功/失败。
```

如果意大利市场尚未创建，Skill 应先引导用户确认市场创建方式。只有在 Shopify schema 和 CLI 支持被验证后，才能生成对应 mutation plan；否则要求用户先在 Shopify Admin 或已验证的受控流程中完成。

## 13. Status Model

### Catalog item status

```text
new
changed
unchanged
pending
in_job
stale
archived
```

### Job item status

```text
pending
translated
needs_review
approved
rejected
blocked
exported
verified
execution_mismatch
stale
```

### Job status

```text
draft
ready
translating
review
approved
exported
failed
archived
```

### Mutation plan status

```text
draft
ready_for_review
approved
executing
executed
failed
cancelled
```

## 14. File Contracts

### Brand Profile

```yaml
brand: Misssixty
mode: shopify_controlled
source_language: en
working_language: zh-CN
default_tone: premium, confident, minimal

markets:
  - market: IT
    locale: it-IT
    enabled: true

shopify:
  enabled: true
  store: misssixty-en
  allow_reads: true
  allow_mutations: false
  max_batch_size: 10
```

### Job Manifest

```json
{
  "job_id": "20261012_it_product_batch",
  "status": "review",
  "mode": "pure",
  "target_locale": "it-IT",
  "market": "IT",
  "source_locale": "en",
  "guideline_ids": [
    "product-title-it",
    "product-description-it"
  ],
  "items": [
    {
      "item_id": "product_1001",
      "status": "needs_review",
      "source_digest": "sha256:...",
      "translation_path": "translations/product_1001.it-IT.md",
      "audit_path": "audits/product_1001.audit.json"
    }
  ]
}
```

### Audit Result

```json
{
  "item_id": "product_1001",
  "status": "needs_review",
  "trust_score": 92,
  "guideline_versions": {
    "product-title-it": 1
  },
  "checks": [
    {
      "rule_id": "locked_glossary_terms",
      "status": "passed",
      "severity": "hard"
    },
    {
      "rule_id": "max_length",
      "status": "warning",
      "severity": "soft",
      "message": "Target title is 74 characters; recommended maximum is 70."
    }
  ]
}
```

### Export Manifest

```json
{
  "export_id": "products_it_20261012_001",
  "job_id": "20261012_it_product_batch",
  "format": "shopify_csv",
  "locale": "it-IT",
  "item_count": 20,
  "approved_only": true,
  "created_at": "2026-10-12T12:00:00+08:00",
  "output_path": "exports/products-it-20261012.csv",
  "content_digest": "sha256:..."
}
```

### Mutation Plan

```json
{
  "batch_id": "BATCH-001",
  "mode": "shopify_controlled",
  "store": "misssixty-en",
  "status": "ready_for_review",
  "target_locale": "it-IT",
  "max_batch_size": 10,
  "operations": [
    {
      "item_id": "product_1001",
      "shopify_resource_id": "gid://shopify/Product/1001",
      "resource_type": "product",
      "field": "title",
      "before": "Leopard-print mini skirt",
      "after": "Minigonna stampata leopard",
      "source_digest": "sha256:...",
      "approval_digest": "sha256:...",
      "operation_kind": "update"
    }
  ]
}
```

## 15. Safety Model

1. Default mode is `pure`.
2. Shopify connector is disabled until setup completes.
3. `allow_reads` controls discovery.
4. `allow_mutations` controls execution readiness.
5. Approval is separate from translation.
6. Mutation approval is separate from export approval.
7. BrandLab never stores Shopify tokens.
8. Shopify CLI owns authentication.
9. Every mutation batch has a plan file.
10. Every mutation result has an execution result file.
11. Failed batch stops subsequent execution.
12. Destructive Shopify operations are out of MVP scope.

## 16. MVP Scope

### Included

1. Setup interview and setup report。
2. Pure Skill Mode。
3. Optional Shopify Controlled Execution Mode。
4. Shopify AI Toolkit + Shopify CLI installation and usage guidance。
5. Local workspace initialization。
6. Brand profile。
7. Guideline files。
8. Glossary。
9. Content import。
10. Content catalog。
11. Source digest and change detection。
12. Existing-content audit。
13. Translation job creation。
14. Guideline-driven translation。
15. Deterministic validation。
16. Trust Score。
17. Review file。
18. Human approval。
19. Export files and manifest。
20. Append-only local history。
21. Mode 3 read-only discovery as a controlled execution prerequisite。
22. Mode 3 approved mutation planning and batch execution。

### Excluded

1. Multi-user admin backend。
2. Role and permission system。
3. Social media generation。
4. Social media API integration。
5. Image generation or image prompt workflow。
6. Expert workbench。
7. Custom Shopify App。
8. Shopify OAuth token management。
9. Background processing。
10. Cloud database。
11. Shopify destructive operations。
12. Automatic retry of failed Shopify mutations。

## 17. MVP Acceptance

### Pure Skill Mode acceptance

1. User initializes a Pure Skill workspace。
2. User imports a local CSV containing product titles、descriptions 和 SEO fields。
3. Skill creates stable IDs and digests。
4. User runs existing-content audit。
5. Skill creates report without changing source files。
6. User creates an `it-IT` job。
7. Skill translates using active guidelines and glossary。
8. Skill runs validators and writes audit JSON。
9. Skill creates `review.md`。
10. User approves eligible items。
11. Hard-rule failures remain blocked。
12. User exports approved items。
13. Skill creates export manifest。
14. Re-importing unchanged source does not duplicate items。
15. Source change marks linked translations stale。

### Mode 3 acceptance

1. Skill detects missing Shopify CLI or AI Toolkit and shows installation guidance。
2. User completes Shopify CLI authentication。
3. Skill generates setup report。
4. Skill performs a small read-only discovery with user confirmation。
5. Skill converts discovery snapshot into catalog items。
6. Translation and approval follow the Pure Mode governance workflow。
7. Approved items can generate a mutation plan。
8. Mutation plan shows resource ID、field、before/after、source digest 和 approval digest。
9. User explicitly approves the plan。
10. User explicitly asks to execute。
11. Skill executes one batch no larger than 10 items。
12. Skill saves raw result、parsed result and history event。
13. Failure stops subsequent batches。
14. No mutation runs without explicit approval。

## 18. Success Metrics

### Operational metrics

1. 100% of jobs have a workflow summary and next action。
2. 100% of hard-rule failures are blocked from approval。
3. 100% of exported items are approved or explicitly forced。
4. 100% of Shopify mutations have a plan file and execution result。
5. 0 Shopify mutations execute without explicit approval。

### Quality metrics

1. Reduce manual terminology corrections。
2. Reduce placeholder and HTML breakage。
3. Reduce repeated brand-tone issues。
4. Make review decisions traceable through source digest、job manifest 和 mutation plan。

## 19. Release Plan

### Phase 1: Core Skill MVP

1. `SKILL.md`。
2. Setup interview。
3. Workspace initialization。
4. Brand、guideline、glossary file contracts。
5. CSV、JSON、JSONL、Markdown、HTML、plain-text import。
6. Translation workflow。
7. Deterministic validation。
8. Review and approval files。
9. Export files and manifest。
10. Append-only history。

### Phase 2: Shopify Controlled Execution

1. Shopify setup detection and guidance。
2. Shopify AI Toolkit integration。
3. Shopify CLI read-only discovery。
4. Controlled mutation plan schema。
5. Approved batch execution。
6. Execution result parsing and verification。

### Phase 3: Workflow Hardening

1. More complete schema validation。
2. Richer Shopify field mapping。
3. Localization memory。
4. Translation diff reports。
5. Guideline linting。
6. Better duplicate and template detection。

### Phase 4: Reusable Packs

1. Reusable brand packs。
2. Reusable market packs。
3. Example library。
4. Guideline templates by industry。
5. Export adapters for downstream systems。

## 20. Open Questions

1. MVP scripts should use Python only, or also support Node?
2. HTML preservation should support inline tags only, or also preserve a whitelist of attributes?
3. Shopify-compatible export should use standard product CSV shape or a simplified translation-import shape?
4. Should bridge-language output be enabled by default for non-primary-language imports?
5. Should duplicate source content share translation results by default?
