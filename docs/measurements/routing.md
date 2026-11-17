# Routing: does the orchestrator ask the right agents?

Measured 12 November 2026 with `backend/app/agents/routing_eval.py --live` on 54 questions labelled by hand (30 English, 24 Egyptian Arabic), in
`backend/tests/data/routing_eval.json`. Each question lists the agents its answer needs (`must`) and the agents that are reasonable to add (`may`).
A route is right when it holds every `must` agent and nothing outside `must` + `may`. The model is `openai/gpt-oss-120b` on Groq, asked live with no
recorded answer standing in; the full rows are in `routing-eval.json`.

| Router | Right | Missed a needed agent | Asked an agent not needed |
|---|---|---|---|
| Language model, first prompt (no network links) | 35 / 54 | 0 | 19 |
| Language model, prompt with the district's links | 46 / 54 | 1 (no usable answer) | 7 |
| **What runs: the model, with the links as a floor and the rules when the model gives nothing** | **47 / 54** | **0** | **7** |
| Rules alone (keywords and links, the fallback when the model is down) | 50 / 54 | 2 | 2 |

What changed between the two model rows: the router prompt now lists which pumps and cooling plants each substation powers, and says not to add an agent
"to be safe". With the first prompt the model asked all three agents for "Which substation is the most loaded?" and asked the cooling agent about SS-03,
which powers no cooling plant.

The floor: when the question names an asset, every agent the network links say it affects is added if the model left it out (a substation brings the water
agent for its pumps and the cooling agent for its plant). In this run the model never left one out, so the floor changed no answer; it is there for the run
where it does. When the model gives no usable route (q47 here), the rules route instead.

The seven extra agents left are all the same kind: the model adds the electricity agent to a question about one other network ("What is the reservoir
level?", "Any problems in the cooling loops?"), or the cooling agent to "What if pump P-01 fails?". That costs time and words, not a wrong figure: each agent only reports its own network.

## How far to trust these numbers

- The set is small and written by the same person who wrote the rules' keywords, so the rules' 50 / 54 is optimistic: the keywords were checked against
  these questions. The model's score is the fairer one; the model never saw the labels.
- One labeller. Some `must`/`may` choices are judgement calls (is electricity "needed" when pressure drops? here it is not).
- Before this is quoted to a utility, their operators should label 100 or more of their own questions, and the run should be repeated on those.

The offline part (rules on the same set, at least 85% right in each language) runs in the test suite:
`tests/test_figures.py::test_the_rule_router_on_the_labelled_questions`.
