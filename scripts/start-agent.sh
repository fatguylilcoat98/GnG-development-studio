#!/usr/bin/env bash
# Safe Server Agent — approved-projects inspection + allow-listed checks only.
# Config: agent_config.json (copy agent_config.example.json). Port: 8894.
cd "$(dirname "$0")/.." && exec python3 agent.py
