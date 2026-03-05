# DECISIONS

Р¤РѕСЂРјР°С‚:

## YYYY-MM-DD Title
Decision:
Why:
Alternatives:
Consequences:

## 2026-02-05 Adopt Bootstrap Architecture v0

Decision:
РџСЂРёРЅСЏС‚ РєР°СЂРєР°СЃ: UI Shell + Orchestrator + ServiceManager + Services + EventBus.

Why:
РќСѓР¶РЅРѕ СѓСЃС‚РѕР№С‡РёРІРѕРµ РѕСЃРЅРѕРІР°РЅРёРµ РґР»СЏ СЂР°СЃС€РёСЂСЏРµРјРѕР№ СЃРёСЃС‚РµРјС‹.

Alternatives:
РњРѕРЅРѕР»РёС‚РЅРѕРµ РїСЂРёР»РѕР¶РµРЅРёРµ СЃ РїСЂСЏРјС‹РјРё РІС‹Р·РѕРІР°РјРё РёР· UI.

Consequences:
Р’СЃРµ РЅРѕРІС‹Рµ С„СѓРЅРєС†РёРё СЂРµР°Р»РёР·СѓСЋС‚СЃСЏ РєР°Рє СЃРµСЂРІРёСЃС‹.
2026-02-05 вЂ” Adopt v1 STOPPING Synchronization Semantics

Decision:
РџСЂРёРЅСЏС‚Р° Р°СЂС…РёС‚РµРєС‚СѓСЂРЅР°СЏ СЃРµРјР°РЅС‚РёРєР° v1 РґР»СЏ РѕСЃС‚Р°РЅРѕРІРєРё СЃРёСЃС‚РµРјС‹ (STOPPING):

РџРµСЂРµС…РѕРґ STOPPING в†’ IDLE СЂР°Р·СЂРµС€С‘РЅ С‚РѕР»СЊРєРѕ РїРѕСЃР»Рµ РїРѕР»СѓС‡РµРЅРёСЏ ServiceStatus=STOPPED РѕС‚ РІСЃРµС… СЃРµСЂРІРёСЃРѕРІ

Р’РІРµРґС‘РЅ РѕР±С‰РёР№ stop_timeout_sec РЅР° С„Р°Р·Сѓ STOPPING

РџСЂРё РёСЃС‚РµС‡РµРЅРёРё stop-timeout СЃРёСЃС‚РµРјР° РїРµСЂРµРІРѕРґРёС‚СЃСЏ РІ ERROR

Why:
Р’ v0 РѕСЃС‚Р°РЅРѕРІРєР° СЃС‡РёС‚Р°Р»Р°СЃСЊ Р·Р°РІРµСЂС€С‘РЅРЅРѕР№ СЃСЂР°Р·Сѓ РїРѕСЃР»Рµ РІС‹Р·РѕРІР° stop_all(),
С‡С‚Рѕ РЅРµРїСЂРёРµРјР»РµРјРѕ РґР»СЏ СЂРµР°Р»СЊРЅС‹С… СЃРµСЂРІРёСЃРѕРІ Рё СѓСЃС‚СЂРѕР№СЃС‚РІ.

Р”Р»СЏ РѕР±РµСЃРїРµС‡РµРЅРёСЏ:

РїСЂРµРґСЃРєР°Р·СѓРµРјРѕСЃС‚Рё РїРѕРІРµРґРµРЅРёСЏ,

РЅР°Р±Р»СЋРґР°РµРјРѕСЃС‚Рё СЃРѕСЃС‚РѕСЏРЅРёСЏ,

РіРѕС‚РѕРІРЅРѕСЃС‚Рё Рє РёРЅС‚РµРіСЂР°С†РёРё Р¶РµР»РµР·Р°,

РѕСЃС‚Р°РЅРѕРІРєР° РґРѕР»Р¶РЅР° РїРѕРґС‚РІРµСЂР¶РґР°С‚СЊСЃСЏ С„Р°РєС‚РёС‡РµСЃРєРёРј СЃРѕСЃС‚РѕСЏРЅРёРµРј СЃРµСЂРІРёСЃРѕРІ.

Alternatives Considered:

РќРµРјРµРґР»РµРЅРЅС‹Р№ РїРµСЂРµС…РѕРґ РІ IDLE (v0 РїРѕРІРµРґРµРЅРёРµ)
в†’ РѕС‚РІРµСЂРіРЅСѓС‚Рѕ: СЃРєСЂС‹РІР°РµС‚ Р·Р°РІРёСЃС€РёРµ/РЅРµРєРѕСЂСЂРµРєС‚РЅРѕ РѕСЃС‚Р°РЅРѕРІР»РµРЅРЅС‹Рµ СЃРµСЂРІРёСЃС‹

РџРµСЂРµС…РѕРґ РІ IDLE СЃ С„Р»Р°РіРѕРј РґРµРіСЂР°РґР°С†РёРё
в†’ РѕС‚РІРµСЂРіРЅСѓС‚Рѕ РІ v1 РґР»СЏ СѓРїСЂРѕС‰РµРЅРёСЏ СЃРµРјР°РЅС‚РёРєРё

РђРІС‚РѕРјР°С‚РёС‡РµСЃРєРёР№ restart СЃРµСЂРІРёСЃРѕРІ
в†’ РІС‹РЅРµСЃРµРЅРѕ Р·Р° scope v1

Consequences:

Orchestrator РѕР±СЏР·Р°РЅ РѕС‚СЃР»РµР¶РёРІР°С‚СЊ СЃС‚Р°С‚СѓСЃС‹ РІСЃРµС… СЃРµСЂРІРёСЃРѕРІ РІРѕ РІСЂРµРјСЏ STOPPING

РљР°Р¶РґС‹Р№ СЃРµСЂРІРёСЃ РѕР±СЏР·Р°РЅ РїСѓР±Р»РёРєРѕРІР°С‚СЊ ServiceStatus=STOPPED РёР»Рё ERROR

РўР°Р№РјР°СѓС‚ РѕСЃС‚Р°РЅРѕРІРєРё СЃС‚Р°РЅРѕРІРёС‚СЃСЏ Р°СЂС…РёС‚РµРєС‚СѓСЂРЅРѕ Р·РЅР°С‡РёРјС‹Рј РїР°СЂР°РјРµС‚СЂРѕРј

РћС€РёР±РєРё РѕСЃС‚Р°РЅРѕРІРєРё СЃС‚Р°РЅРѕРІСЏС‚СЃСЏ РЅР°Р±Р»СЋРґР°РµРјС‹РјРё Рё РЅРµ РјРѕРіСѓС‚ Р±С‹С‚СЊ вЂњРїСЂРѕРіР»РѕС‡РµРЅС‹вЂќ

Scope:

РџСЂРёРјРµРЅСЏРµС‚СЃСЏ РЅР°С‡РёРЅР°СЏ СЃ РІРµСЂСЃРёРё v1

РќРµ РІР»РёСЏРµС‚ РЅР° v0 (v0 Р·Р°РєСЂС‹С‚Р° С‡РµСЂРµР· ACCEPT_v0.md)

## 2026-02-11 вЂ” Adopt Service Roles (job / daemon)

### Context

РЎРёСЃС‚РµРјР° РЅР°С‡Р°Р»Р° РІРєР»СЋС‡Р°С‚СЊ РїРѕСЃС‚РѕСЏРЅРЅС‹Рµ СЃРµСЂРІРёСЃС‹
(rtsp_health, rtsp_ingest, hardware control),
РєРѕС‚РѕСЂС‹Рµ РґРѕР»Р¶РЅС‹ СЂР°Р±РѕС‚Р°С‚СЊ РЅРµР·Р°РІРёСЃРёРјРѕ РѕС‚ run-cycle РІС‹С‡РёСЃР»РёС‚РµР»СЊРЅС‹С… Р·Р°РґР°С‡.

Р’ Orchestrator v3 RUNNING РѕС‚СЂР°Р¶Р°Р» Р°РєС‚РёРІРЅРѕСЃС‚СЊ Р»СЋР±С‹С… СЃРµСЂРІРёСЃРѕРІ,
С‡С‚Рѕ РїСЂРёРІРѕРґРёР»Рѕ Рє "РІРµС‡РЅРѕРјСѓ RUNNING" РїСЂРё РЅР°Р»РёС‡РёРё daemon.

### Decision

Р’РІРµСЃС‚Рё СЂРѕР»Рё СЃРµСЂРІРёСЃРѕРІ:

- job вЂ” СѓС‡Р°СЃС‚РІСѓРµС‚ РІ run-cycle
- daemon вЂ” РґРѕР»РіРѕР¶РёРІСѓС‰РёР№ СЃРµСЂРІРёСЃ

Orchestrator RUNNING С‚РµРїРµСЂСЊ РѕС‚СЂР°Р¶Р°РµС‚ С‚РѕР»СЊРєРѕ job run-cycle.

### Consequences

+ РџРѕРґРґРµСЂР¶РєР° РїРѕСЃС‚РѕСЏРЅРЅС‹С… СЃРµСЂРІРёСЃРѕРІ
+ UI РєРѕСЂСЂРµРєС‚РЅРѕ РѕС‚РѕР±СЂР°Р¶Р°РµС‚ СЃРѕСЃС‚РѕСЏРЅРёРµ
+ РњР°СЃС€С‚Р°Р±РёСЂСѓРµРјРѕСЃС‚СЊ СЃРёСЃС‚РµРјС‹

- РџРѕСЏРІР»СЏРµС‚СЃСЏ РЅРµРѕР±С…РѕРґРёРјРѕСЃС‚СЊ С„РёРєСЃРёСЂРѕРІР°С‚СЊ role РІ РїСЂРѕС„РёР»СЏС…

At application startup, daemon services may be auto-started via Orchestrator.start_daemons().
This does not activate RUNNING state and does not start job run-cycle.

## 2026-03-05 Adopt 3-stage orchestration flow draft

Decision:
Adopt draft architecture with explicit preparation, readiness, and test-run stages. Start with documentation before code refactor.

Why:
Current flow does not cleanly separate artifact preparation from operational readiness and test execution.

Alternatives:
Keep single start/stop run-cycle semantics without stage boundaries.

Consequences:
Future orchestrator refactor will add stage-aware states and commands. Mayak integration proceeds via temporary stub mode and later real transport implementation.

