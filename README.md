# BrandLab 翻译助手

BrandLab 是一个 Codex Skill，帮助运营人员翻译品牌内容，并在人工确认后再导出或写回 Shopify。

它适合处理：

- 商品标题和描述
- SEO 标题和描述
- 页面内容
- 博客内容
- 需要遵守品牌规则和术语的内容

BrandLab 的重点不是“机器直接翻译”，而是：

1. 按品牌规则和术语表翻译。
2. 自动检查占位符、HTML、术语和长度。
3. 生成人工审核文件。
4. 只导出或写回你明确批准的内容。

## 安装一次

在 Codex 里发送：

```text
请安装这个 Codex skill：
https://github.com/jundihuang/agenticplus/tree/v0.2.1/skills/brandlab
```

安装后执行一次：

```bash
python3 -m pip install PyYAML
```

然后新开一个 Codex 会话。

## 日常使用

你通常不需要记住命令，直接对 Codex 说你的目标即可。

### 1. 初始化品牌工作区

```text
用 BrandLab 初始化一个翻译工作区。
品牌是 Miss Sixty。
源语言是英文，工作语言是中文，目标市场是意大利，目标语言是意大利语。
```

### 2. 翻译文件

把文件放到 `brandlab/inbox/`，然后说：

```text
用 BrandLab 把 inbox 里的商品内容从英文翻译成意大利语。
请先做检查和审核文件，不要导出。
```

### 3. 人工审核

BrandLab 会生成：

```text
brandlab/jobs/<任务ID>/review.md
```

你可以打开这个文件检查：

- 原文
- 译文
- 校验结果
- 建议处理方式

审完后告诉 Codex：

```text
我已确认这些翻译通过，请批准全部内容，并导出 Shopify CSV。
```

也可以只批准部分：

```text
只批准前 10 条，其他保留待审核。
```

## 写回 Shopify

BrandLab 支持可选的 Shopify 回写，但默认不会自动发布。

你可以说：

```text
连接 Shopify 测试店铺，读取商品内容，准备意大利语翻译。
```

翻译审核通过后，再说：

```text
为已批准的意大利语翻译生成 Shopify 回写计划。
```

BrandLab 会生成一个回写计划，展示要更新哪些商品和字段。你确认后，最后说：

```text
我确认执行这一批 Shopify 更新。
```

### Shopify 回写限制

- 只更新商品的 `title`、`descriptionHtml`、`seo.title` 和 `seo.description`。
- 每批最多 10 条。
- 必须先有人工批准。
- 必须有单独的执行确认。
- 失败后会停止，不会自动重试。
- 不会执行删除操作。
- Shopify 登录和 token 由 Shopify CLI 管理，BrandLab 不保存 token。

## 技术安装说明

普通内容翻译只需要 Python 3.10+ 和 PyYAML。

如果要连接 Shopify，还需要 Node.js 18+、Shopify CLI 和 Shopify AI Toolkit。详细步骤见：

```text
skills/brandlab/docs/SHOPIFY-SETUP.md
```
