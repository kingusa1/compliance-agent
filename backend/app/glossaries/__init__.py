"""Domain glossaries for AssemblyAI word_boost (L9).

Submodules:
  watt_terms — UK energy-industry base vocabulary (LOA, MOP, MPAN, ...).
  suppliers  — per-supplier branded names (E.ON, BG Lite, Pozitive, ...).
  loader     — `load_supplier_glossary(supplier)` merges the two lists.

Used by `app.assemblyai_transcription.transcribe_audio_assemblyai` to bias
the STT toward terms that mistranscribe on British phone audio.
"""
