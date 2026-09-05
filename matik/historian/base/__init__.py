"""Shared historian infrastructure: the generic crawler base and run_historian.

This package hosts the source-agnostic pieces of the historian pipeline so each
source only implements its irreducible hooks:

- ``crawler.BaseCrawler`` — the producer/consumer/queue batch-dispatch skeleton
  (``dispatch``/``_dispatch_async``/``_consumer``/``_process_batch``) plus the
  ``CrawlerError``/``CrawlerResult`` types. Subclasses implement the tracker,
  cursor, fetch (producer), and publish hooks.
- ``runner.run_historian`` — the common historian ``main()`` shell (config
  merges, logging, Telescope/metrics, client + publisher construction, job
  metrics, crawler dispatch, result→return-code mapping, Telescope shutdown).
"""
