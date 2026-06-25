from pillar.config import PillarConfig

# Re-export the framework config as the app-level config singleton.
# Add app-specific settings here.
settings = PillarConfig.load("pillar.toml")
