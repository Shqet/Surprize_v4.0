# Project Documentation Index

## TL;DR
РџСЂРѕРµРєС‚ вЂ” Desktop РїСЂРёР»РѕР¶РµРЅРёРµ (Windows 10/11):
РћРїРµСЂР°С‚РѕСЂСЃРєР°СЏ РѕР±РѕР»РѕС‡РєР° + Orchestrator + Services.

UI РЅРёС‡РµРіРѕ РЅРµ Р·РЅР°РµС‚ РїСЂРѕ СѓСЃС‚СЂРѕР№СЃС‚РІР° Рё subprocess.
Р’СЃСЏ Р»РѕРіРёРєР° С‚РѕР»СЊРєРѕ С‡РµСЂРµР· Orchestrator Рё Services.

Р”РѕРєСѓРјРµРЅС‚Р°С†РёСЏ РїСЂРµРґРЅР°Р·РЅР°С‡РµРЅР° РІ РїРµСЂРІСѓСЋ РѕС‡РµСЂРµРґСЊ РґР»СЏ РР-Р°СЃСЃРёСЃС‚РµРЅС‚Р°.

---

## Sources of Truth (РіР»Р°РІРЅС‹Рµ РґРѕРєСѓРјРµРЅС‚С‹)

1) VISION.md вЂ” С†РµР»СЊ РїСЂРѕРµРєС‚Р°, РіСЂР°РЅРёС†С‹
2) ARCH.md вЂ” Р°СЂС…РёС‚РµРєС‚СѓСЂР°, СЃР»РѕРё, РїРѕС‚РѕРєРё
3) CONTRACTS.md вЂ” РєРѕРЅС‚СЂР°РєС‚С‹ СЃРѕР±С‹С‚РёР№ Рё СЃРµСЂРІРёСЃРѕРІ
4) LOG_CODES.md вЂ” СЃРїСЂР°РІРѕС‡РЅРёРє Р»РѕРі-РєРѕРґРѕРІ
4) AI_RULES.md вЂ” РїСЂР°РІРёР»Р° РґР»СЏ РР-СЂРµР°Р»РёР·Р°С†РёРё

Р­С‚Рё С„Р°Р№Р»С‹ РёРјРµСЋС‚ РїСЂРёРѕСЂРёС‚РµС‚ РЅР°Рґ РІСЃРµРј РѕСЃС‚Р°Р»СЊРЅС‹Рј.

---

## Operational Docs

- RUNBOOK.md вЂ” СЃР±РѕСЂРєР°, Р·Р°РїСѓСЃРє, Р»РѕРіРё, С‚РёРїРѕРІС‹Рµ РїСЂРѕР±Р»РµРјС‹
- ROADMAP.md вЂ” РѕС‡РµСЂРµРґСЊ Р·Р°РґР°С‡
- DECISIONS.md вЂ” Р¶СѓСЂРЅР°Р» СЂРµС€РµРЅРёР№
- ARCH_ORCHESTRATOR_3_STAGE_v1.md - Draft design for preparation/readiness/test flow with Mayak stub mode
- ARCH_TEST_SESSION_SYNC_v1.md - Test session synchronization model (video + gps tx + trajectory timeline)
- SERVICES.md вЂ” СЂРµРµСЃС‚СЂ СЃРµСЂРІРёСЃРѕРІ
- DEV_REPORT_TEMPLATE.md вЂ” РѕР±СЏР·Р°С‚РµР»СЊРЅС‹Р№ С€Р°Р±Р»РѕРЅ РѕС‚С‡С‘С‚Р° СЂР°Р·СЂР°Р±РѕС‚С‡РёРєР° РґР»СЏ РїСЂРёС‘РјРєРё Р°СЂС…РёС‚РµРєС‚РѕСЂРѕРј
- ARCH_ACCEPTANCE_TEMPLATE.md вЂ” С€Р°Р±Р»РѕРЅ РѕС‚РІРµС‚Р° Р°СЂС…РёС‚РµРєС‚РѕСЂР° (Р°РєС‚ РїСЂРёС‘РјРєРё РІРµСЂСЃРёРё/СЌС‚Р°РїР° РїРѕ Developer Report)
- acceptance/ вЂ” РїСЂРёРЅСЏС‚С‹Рµ Architect Acceptance (Р·Р°РєСЂС‹С‚С‹Рµ РІРµСЂСЃРёРё Рё С„Р°Р·С‹ РїСЂРѕРµРєС‚Р°)


---

## How to feed chats

### Architect Chat
- VISION.md
- ARCH.md
- CONTRACTS.md

### Developer Chat
- ARCH.md
- CONTRACTS.md
- AI_RULES.md
- RUNBOOK.md

### Fixer Chat
- RUNBOOK.md
- CONTRACTS.md
- СЃСЃС‹Р»РєР° РЅР° Р±Р°Рі-СЂРµРїРѕСЂС‚

---

## Core Principles

- UI в†’ Orchestrator в†’ Services в†’ Drivers/Adapters
- РЎРµСЂРІРёСЃС‹ РЅРµР·Р°РІРёСЃРёРјС‹ РґСЂСѓРі РѕС‚ РґСЂСѓРіР°
- РЎРѕСЃС‚РѕСЏРЅРёРµ СЃРёСЃС‚РµРјС‹ СѓРїСЂР°РІР»СЏРµС‚СЃСЏ Orchestrator
- Р’СЃС‘ РѕР±С‰РµРЅРёРµ вЂ” СЃРѕР±С‹С‚РёСЏРјРё


