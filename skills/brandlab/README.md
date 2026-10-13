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

BrandLab 可以读取 Shopify 商品内容，并把批准后的翻译写回商品。

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

它不会：

- 自动发布
- 自动重试失败批次
- 执行删除操作
- 保存或显示 Shopify token

每批最多更新 10 条。

## 结果在哪里

| 内容 | 位置 |
| --- | --- |
| 待翻译文件 | `brandlab/inbox/` |
| 内容清单 | `brandlab/content/catalog.jsonl` |
| 人工审核文件 | `brandlab/jobs/<任务ID>/review.md` |
| 导出文件 | `brandlab/exports/` |
| Shopify 发现内容 | `brandlab/shopify/discovery/` |
| Shopify 回写计划 | `brandlab/shopify/mutation-plans/` |
| 执行记录 | `brandlab/shopify/execution-results/` |
| 操作历史 | `brandlab/history/events.jsonl` |

## 需要命令时

日常使用不需要记住命令。安装、调试或排查问题时，可以让 Codex：

```text
用 BrandLab 检查当前工作区状态，并告诉我下一步。
```

Shopify 安装和授权说明见 `docs/SHOPIFY-SETUP.md`。
