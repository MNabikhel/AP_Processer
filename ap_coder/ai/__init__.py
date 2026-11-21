"""Reading and AI: the readers, the local and Azure models, second opinions, learning, accuracy and the pipeline.

``pipeline`` takes one invoice from its file to the review queue (``finalise_coding``). Reading: ``reading`` (every
reader's result merged), ``page_reader`` (OvisOCR2, the vision model; its queue ``page_worker``), ``extraction`` (the
text the coding reads), ``imaging`` (page pictures for a chat model), ``figures`` (two readings compared), and Azure
Document Intelligence once an Admin turns it on (``azure_check``, ``di_http``). Coding: ``inference`` (the chat
model), ``local_llm`` (LM Studio or Ollama found, its model picked), ``prompts``; with no model ``offline_coder``.
Second opinions: ``verifier`` (the AI reviewer) and ``corrections`` (the ones the math confirms). Learning:
``suggest`` (GL accounts from past approvals), ``line_match``, ``lessons`` (AP's corrections with their reasons),
``supplier_prior`` (a supplier's past account strings), ``history`` (the ERP's past coding), ``training_export``.
Accuracy: ``evaluation``, ``labels``.
"""
