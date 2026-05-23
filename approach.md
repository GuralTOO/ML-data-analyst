Project GOAL:

Build an agentic search and interaction platform to find the best ML datasets for an open ended user query and follow up tailoring. The experience is a chat with an agent that finds good datasets. 

The tech
We will be using Nimble for web search when we need to find the right datasets on huggingface.
We will be using Huggingface MCP as well to find and interact with the datasets. 
We will be using an open-source agentic harness such as PI to scale up parallel agent queries to analyze dataset semantics
We will be using clickhouse to get a deeper understanding of select datasets, working with real data. Agents will interact with clickhouse that runs on real data. 

The output
The output is a lightweight, local only platform with a local backend in the /backend folder and vite-react front-end in the /frontend folder. The locally run platform will use the chosen open-source agent harness and open-router for model access. The front-end will have agent-chat UI.

Overal workflow is such -

The main agent interactds with the user
The same agent will use NIMBLE to find datasets on HuggingFace And Shortlist the relevant ones. This search is visible to the user as well.
We are exposing specific tools to the agent to conduct search and load up the results. (GOOGLE SEARCH and AI Search by Nimbble + Extract)
Based on the search results, the main agent together with the user decides which dataset to analyze deeply.
Selected datasets are given a dedicated agent as well as a separate docker set-up to use clickhouse as an interaction layer with that dataset.
Dedicated agents use HuggingFace and ClickHouse to load up specific datasets for further analysis. Clickhouse serves as the interaction layer for the agent. We are building specific tools that we will expose to the agent to interact with the dataset through clickhouse. The objective of the tools is to be flexible for different dataset types.
Main agent communicates the results back to the user. The user can also chat with the dedicated agents for each selected dataset individually. The UI uses modals to show the agent working on each dataset individually. Essentially each deep dive on dataset gets its own agent that is persistent, and maintains its context. The agent will work live on the task given to it, once it is done, the user can interact with that agent directly through the modal.