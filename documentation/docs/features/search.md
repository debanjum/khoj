---
sidebar_position: 3
---

# Search

Take advantage of super fast search to find relevant notes and documents from your Second Brain.

### Use
1. Open Khoj Search
  - **On Web**: Open https://app.khoj.dev/ in your web browser
  - **On Obsidian**: Click the *Khoj search* icon 🔎 on the [Ribbon](https://help.obsidian.md/User+interface/Workspace/Ribbon) or Search for *Khoj: Search* in the [Command Palette](https://help.obsidian.md/Plugins/Command+palette)
  - **On Emacs**: Run `M-x khoj <user-query>`
2. Query using natural language to find relevant entries from your knowledge base. Use [query filters](/miscellaneous/query-filters) to limit entries to search

### Demo
![](/img/search_agents_markdown.png ':size=400px')


### Implementation Overview
A bi-encoder models is used to create meaning vectors (aka vector embeddings) of your documents and search queries.
1. When you sync you documents with Khoj, it uses the bi-encoder model to create and store meaning vectors of (chunks of) your documents
2. When you initiate a natural language search the bi-encoder model converts your query into a meaning vector and finds the most relevant document chunks for that query by comparing their meaning vectors.
3. The slower but higher-quality cross-encoder model is than used to re-rank these documents for your given query.

### Setup (Self-Hosting)
You are **not required** to configure the search model config when self-hosting. Khoj sets up decent default local search model config for general use.

You may want to configure this if you need better multi-lingual search, want to experiment with different, newer models or the default models do not work for your use-case.

You can use bi-encoder models downloaded locally [from Huggingface](https://huggingface.co/models?library=sentence-transformers), served via the [HuggingFace Inference API](https://endpoints.huggingface.co/), OpenAI API, Azure OpenAI API or any OpenAI Compatible API like Ollama, LiteLLM etc. Follow the steps below to configure your search model:

1. Open the [SearchModelConfig](http://localhost:42110/server/admin/database/searchmodelconfig/) page on your Khoj admin panel.
2. Hit the Plus button to add a new model config or click the id of an existing model config to edit it.
3. Set the `biencoder` field to the name of the bi-encoder model supported [locally](https://huggingface.co/models?library=sentence-transformers) or via the API you configure.
4. Set the `Embeddings inference endpoint api key` to your OpenAI API key and `Embeddings inference endpoint type` to `OpenAI` to use an OpenAI embedding model.
5. Also set the `Embeddings inference endpoint` to your Azure OpenAI or OpenAI compatible API URL to use the model via those APIs.
6. Ensure the search model config you want to use is the **only one** that has `name` field set to `default`[^1].
7. Save the search model configs and restart your Khoj server to start using your new, updated search config.

:::info
You will need to re-index all your documents if you want to use a different bi-encoder model.
:::

:::info
You may need to tune the `Bi encoder confidence threshold` field for each bi-encoder to get appropriate number of documents for chat with your Knowledge base.

Confidence here is a normalized measure of semantic distance between your query and documents. The confidence threshold limits the documents returned to chat that fall within the distance specified in this field. It can take values between 0.0 (exact overlap) and 1.0 (no meaning overlap).
:::

[^1]: Khoj uses the first search model config named `default` it finds on startup as the search model config for that session
