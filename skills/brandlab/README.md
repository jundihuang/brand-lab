# BrandLab 翻译助手

BrandLab 帮助运营人员在 Codex 里翻译品牌内容。它会应用品牌规则和术语表，自动检查常见问题，生成人工审核文件，并且只导出或写回你明确批准的内容。

## 你可以对 Codex 说

### 初始化

```text
用 BrandLab 初始化一个翻译工作区。
品牌是 Miss Sixty。
源语言是英文，工作语言是中文，目标市场是意大利，目标语言是意大利语。
```

### 翻译本地文件

把 CSV、JSON、JSONL、Markdown、HTML 或文本文件放到 `brandlab/inbox/`，然后说：

```text
用 BrandLab 把 inbox 里的商品内容从英文翻译成意大利语。
先做检查和审核文件，不要导出。
```

### 审核

打开：

```text
brandlab/jobs/<任务ID>/review.md
```

确认后说：

```text
我确认这些翻译通过，请批准全部内容。
```

或：

```text
只批准商品标题，其他保留待审核。
```

### 导出

```text
请把已批准的内容导出为 Shopify CSV。
```

结果会保存到：

```text
brandlab/exports/
```

## Shopify 回写

BrandLab 可以读取 Shopify 商品内容，并把批准后的翻译写回商品。已有市场翻译修复会使用 Shopify Translation API，按市场和语言写入，不会覆盖商品默认字段。

你可以说：

```text
连接 Shopify 测试店铺，读取商品内容，准备意大利语翻译。
```

审核通过后：

```text
为已批准的意大利语翻译生成 Shopify 回写计划。
```

确认执行时：

```text
我确认执行这一批 Shopify 更新。
```

BrandLab 只会更新：

- 商品标题
- 商品描述
- SEO 标题
- SEO 描述
- 市场翻译内容，使用 Shopify Translation API

它不会：

- 自动发布
- 自动重试失败批次
- 执行删除操作
- 保存或显示 Shopify token

每批最多更新 10 条。

## 检查已有市场翻译

如果一个市场已经有翻译，例如墨西哥市场，你可以对 Codex 说：

```text
检查 MX 市场的现有翻译，先不要修改 Shopify。
```

BrandLab 会生成翻译体检报告，告诉你：

- 哪些内容可以直接保留
- 哪些内容需要修复或重新翻译
- 哪些内容只需要本地化调整
- 哪些内容需要人工判断

如果找不到英文源内容，BrandLab 会切换为“仅目标内容检查”。它仍然能检查语言质量、品牌规则、SEO、占位符和格式，但不会假装能判断“错译、漏译或多译”。

需要更深入的检查时，可以说：

```text
对 MX 体检报告做 AI Review，但不要修改 Shopify。
```

BrandLab 会把 AI 建议合并回体检报告。没有英文源内容时，它只会给出语法、表达、品牌语气、地区习惯和 SEO 等建议，不会直接断定“错译、漏译或多译”。

你确认要处理后，可以说：

```text
把报告里需要重新翻译的商品标题创建成修复任务。
```

修复任务也会先生成审核文件；你批准前，它不会导出或修改 Shopify。

## 新增市场

你可以说：

```text
新增 US 市场，语言是 English。
```

BrandLab 会先给出一份新增市场计划，包括：

- Shopify 市场和语言是否可用
- 应该做翻译，还是只做本地化检查
- 品牌配置的调整内容
- 推荐的品牌规则和术语表
- 后续内容扫描或体检计划

`en → en-US` 只做本地化检查，不会生成“英文翻译成英文”的任务。

你确认后，再说：

```text
确认新增这个市场。
```

## 结果在哪里

| 内容 | 位置 |
| --- | --- |
| 待翻译文件 | `brandlab/inbox/` |
| 内容清单 | `brandlab/content/catalog.jsonl` |
| 人工审核文件 | `brandlab/jobs/<任务ID>/review.md` |
| 导出文件 | `brandlab/exports/` |
| Shopify 发现内容 | `brandlab/shopify/discovery/` |
| 已有市场翻译 | `brandlab/shopify/existing-translations/` |
| 翻译体检报告 | `brandlab/reports/` |
| 修复计划 | `brandlab/corrections/` |
| 新增市场计划 | `brandlab/onboarding/` |
| 多市场汇总 | `brandlab/reports/market-translation-summary.md` |
| Shopify 回写计划 | `brandlab/shopify/mutation-plans/` |
| 执行记录 | `brandlab/shopify/execution-results/` |
| 操作历史 | `brandlab/history/events.jsonl` |

## 需要命令时

日常使用不需要记住命令。安装、调试或排查问题时，可以让 Codex：

```text
用 BrandLab 检查当前工作区状态，并告诉我下一步。
```

Shopify 安装和授权说明见 `docs/SHOPIFY-SETUP.md`。
