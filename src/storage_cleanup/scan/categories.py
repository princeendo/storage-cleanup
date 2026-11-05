"""Category IDs (the "what is it" lens) and their display names."""

from typing import Final

AI_TOOLING: Final = "ai-tooling"
DOCKER: Final = "docker"
APPLICATIONS: Final = "applications"
APP_DATA: Final = "app-data"
LEFTOVER_APP_DATA: Final = "leftover-app-data"
HOMEBREW: Final = "homebrew"
PACKAGES: Final = "packages"
DEV_CACHES: Final = "dev-caches"
PROJECTS: Final = "projects"
DOWNLOADS: Final = "downloads"
HOME: Final = "home"

DISPLAY_NAMES: Final[dict[str, str]] = {
    AI_TOOLING: "AI tooling",
    DOCKER: "Docker",
    APPLICATIONS: "Applications",
    APP_DATA: "App data",
    LEFTOVER_APP_DATA: "Data from removed apps",
    HOMEBREW: "Homebrew",
    PACKAGES: "Command-line packages",
    DEV_CACHES: "Developer caches",
    PROJECTS: "Projects",
    DOWNLOADS: "Downloads",
    HOME: "Home folder",
}
