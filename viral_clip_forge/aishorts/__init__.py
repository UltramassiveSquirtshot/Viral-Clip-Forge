"""
AI Shorts — third, fully-synthetic content format.

Each Short is built from AI-generated images (made by hand on Leonardo.ai from
prompts this pipeline supplies) plus an edge-tts narrator voice, assembled with
a bright/punchy Ken Burns montage and karaoke word-by-word captions.

No third-party footage, no paid APIs. The narration script is produced by the
Cowork agent and queued on Drive as `ai_shorts_scripts.json` (same queue model
as the ranker). Isolated from the CC-BY and ranker pipelines.
"""
