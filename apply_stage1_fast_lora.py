#!/usr/bin/env python3
"""
TerraVeil Stage 1: speed up collision-free LoRa polling.

Run from the TerraVeil repository root:
    python3 apply_stage1_fast_lora.py

The script edits only:
    nodes_source_code/ug_node1.cpp
    nodes_source_code/ug_node2.cpp
    nodes_source_code/ld_node1.cpp
    nodes_source_code/main_hub.cpp

It keeps the existing hub-controlled POLL -> DATA -> ACK architecture and
does not change SF7 / 125 kHz / CR 4/5 radio settings.
"""

from pathlib import Path

ROOT = Path.cwd()

FILES = {
    "ug1": ROOT / "nodes_source_code" / "ug_node1.cpp",
    "ug2": ROOT / "nodes_source_code" / "ug_node2.cpp",
    "ld": ROOT / "nodes_source_code" / "ld_node1.cpp",
    "hub": ROOT / "nodes_source_code" / "main_hub.cpp",
}

for name, path in FILES.items():
    if not path.exists():
        raise SystemExit(f"Missing {name}: {path}\nRun this script from the TerraVeil repo root.")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(
            f"{label}: expected exactly 1 match, found {count}. "
            "The repository may have changed since this script was prepared."
        )
    return text.replace(old, new, 1)


def write_if_changed(path: Path, original: str, updated: str) -> None:
    if updated == original:
        print(f"[UNCHANGED] {path}")
        return
    path.write_text(updated)
    print(f"[UPDATED]   {path}")


# ---------------------------------------------------------------------------
# UG-01 / UG-02
# ---------------------------------------------------------------------------
for key in ("ug1", "ug2"):
    path = FILES[key]
    original = path.read_text()
    text = original

    text = replace_once(
        text,
        "const unsigned long ACK_TIMEOUT_MS = 1500;\n"
        "const unsigned long POLL_TURNAROUND_MS = 20;",
        "const unsigned long ACK_TIMEOUT_MS = 600;\n"
        "const unsigned long POLL_TURNAROUND_MS = 12;",
        f"{path.name} timing constants",
    )

    # ACK wait loop: tighter polling of the radio.
    text = replace_once(
        text,
        "        delay(2);\n"
        "    }\n\n"
        "    Serial.println(\"[ACK] Timeout\");",
        "        delay(1);\n"
        "    }\n\n"
        "    Serial.println(\"[ACK] Timeout\");",
        f"{path.name} ACK-loop delay",
    )

    # Main loop: check the radio more frequently.
    text = replace_once(
        text,
        "    delay(5);\n"
        "}",
        "    delay(1);\n"
        "}",
        f"{path.name} main-loop delay",
    )

    write_if_changed(path, original, text)


# ---------------------------------------------------------------------------
# LD-01
# ---------------------------------------------------------------------------
path = FILES["ld"]
original = path.read_text()
text = original

text = replace_once(
    text,
    "const int ADC_SAMPLES = 20;",
    "const int ADC_SAMPLES = 10;",
    "ld_node1.cpp ADC sample count",
)

text = replace_once(
    text,
    "const unsigned long ACK_TIMEOUT_MS      = 1500;\n"
    "const unsigned long POLL_TURNAROUND_MS  = 20;",
    "const unsigned long ACK_TIMEOUT_MS      = 600;\n"
    "const unsigned long POLL_TURNAROUND_MS  = 12;",
    "ld_node1.cpp timing constants",
)

# Potentiometer averaging: 10 x 1 ms instead of 20 x 2 ms.
text = replace_once(
    text,
    "        delay(2);\n"
    "    }\n\n\n"
    "    return\n"
    "        total",
    "        delay(1);\n"
    "    }\n\n\n"
    "    return\n"
    "        total",
    "ld_node1.cpp potentiometer averaging delay",
)

# ACK wait loop.
text = replace_once(
    text,
    "        delay(2);\n"
    "    }\n\n"
    "    Serial.println(\n"
    "        \"[ACK] Timeout\"",
    "        delay(1);\n"
    "    }\n\n"
    "    Serial.println(\n"
    "        \"[ACK] Timeout\"",
    "ld_node1.cpp ACK-loop delay",
)

# Main loop.
text = replace_once(
    text,
    "    delay(5);\n"
    "}",
    "    delay(1);\n"
    "}",
    "ld_node1.cpp main-loop delay",
)

write_if_changed(path, original, text)


# ---------------------------------------------------------------------------
# HOST-01 / main hub
# ---------------------------------------------------------------------------
path = FILES["hub"]
original = path.read_text()
text = original

text = replace_once(
    text,
    "// One complete acquisition round begins every 5 seconds while the\n"
    "// network is healthy. Only one field node is allowed to answer at a time.\n"
    "const unsigned long POLLING_ROUND_INTERVAL_MS = 5000UL;\n"
    "const unsigned long POLL_RESPONSE_TIMEOUT_MS  = 1200UL;\n"
    "const unsigned long INTER_POLL_GUARD_MS       = 80UL;\n"
    "const unsigned long RETRY_GUARD_MS            = 150UL;",
    "// Fast/stable target: one complete acquisition round per second while the\n"
    "// network is healthy. Only one field node is allowed to answer at a time.\n"
    "const unsigned long POLLING_ROUND_INTERVAL_MS = 1000UL;\n"
    "const unsigned long POLL_RESPONSE_TIMEOUT_MS  = 750UL;\n"
    "const unsigned long INTER_POLL_GUARD_MS       = 25UL;\n"
    "const unsigned long RETRY_GUARD_MS            = 75UL;",
    "main_hub.cpp polling constants",
)

text = replace_once(
    text,
    "    // ACK FIRST.\n"
    "    // The field nodes wait only 1500 ms, so acknowledge the\n",
    "    // ACK FIRST.\n"
    "    // The field nodes wait only 600 ms, so acknowledge the\n",
    "main_hub.cpp ACK comment",
)

text = replace_once(
    text,
    "        delay(2);\n"
    "    }\n\n"
    "    pollTimeouts++;",
    "        delay(1);\n"
    "    }\n\n"
    "    pollTimeouts++;",
    "main_hub.cpp poll wait-loop delay",
)

text = replace_once(
    text,
    "    // Start immediately on boot, then approximately every 5 seconds.",
    "    // Start immediately on boot, then approximately every 1 second.",
    "main_hub.cpp loop comment",
)

# Final loop delay.
text = replace_once(
    text,
    "    printNetworkHealth();\n\n"
    "    delay(2);\n"
    "}",
    "    printNetworkHealth();\n\n"
    "    delay(1);\n"
    "}",
    "main_hub.cpp main-loop delay",
)

write_if_changed(path, original, text)

print()
print("Stage 1 LoRa timing update applied.")
print("Recommended next steps:")
print("  1. Review: git diff -- nodes_source_code")
print("  2. Flash main_hub.cpp first.")
print("  3. Flash UG-01, UG-02, then LD-01.")
print("  4. Confirm one fresh packet per node per ~1 second.")
print("  5. Watch ACK timeouts / poll timeouts for 2-3 minutes.")


