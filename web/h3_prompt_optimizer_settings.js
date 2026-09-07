import {app} from "/scripts/app.js";
import {
    normalizePromptOptimizerApiFormat,
    normalizePromptOptimizerBackend,
} from "./h3_prompt_optimizer_core.mjs?v=0.6.2";

export const PROMPT_OPTIMIZER_SETTING_IDS = Object.freeze({
    backend: "MiniMaxH3ContexLoop.PromptOptimizer.Backend",
    mcpProvider: "MiniMaxH3ContexLoop.PromptOptimizer.McpProvider",
    apiFormat: "MiniMaxH3ContexLoop.PromptOptimizer.ApiFormat",
    apiUrl: "MiniMaxH3ContexLoop.PromptOptimizer.ApiUrl",
    apiKey: "MiniMaxH3ContexLoop.PromptOptimizer.ApiKey",
    model: "MiniMaxH3ContexLoop.PromptOptimizer.Model",
    allowMedia: "MiniMaxH3ContexLoop.PromptOptimizer.AllowMedia",
});

function settingValue(id, fallback) {
    return app.ui?.settings?.getSettingValue?.(id) ?? fallback;
}

export function promptOptimizerBackend() {
    return normalizePromptOptimizerBackend(
        settingValue(PROMPT_OPTIMIZER_SETTING_IDS.backend, "direct"));
}

export function promptOptimizerMcpProvider() {
    const value = String(settingValue(
        PROMPT_OPTIMIZER_SETTING_IDS.mcpProvider, "codex") ?? "").trim().toLowerCase();
    return /^[a-z][a-z0-9_-]*$/.test(value) ? value : "codex";
}

export function promptOptimizerDirectConfig() {
    return {
        api_format: normalizePromptOptimizerApiFormat(settingValue(
            PROMPT_OPTIMIZER_SETTING_IDS.apiFormat, "openai")),
        api_url: String(settingValue(PROMPT_OPTIMIZER_SETTING_IDS.apiUrl, "") ?? ""),
        api_key: String(settingValue(PROMPT_OPTIMIZER_SETTING_IDS.apiKey, "") ?? ""),
        model: String(settingValue(PROMPT_OPTIMIZER_SETTING_IDS.model, "") ?? ""),
        allow_media: settingValue(PROMPT_OPTIMIZER_SETTING_IDS.allowMedia, false) === true,
    };
}

export async function openPromptOptimizerSettings() {
    const command = app?.extensionManager?.command;
    if (!command || typeof command.execute !== "function") return false;
    try {
        await command.execute("Comfy.ShowSettingsDialog");
        return true;
    } catch {
        return false;
    }
}

function notifyChanged() {
    globalThis.dispatchEvent?.(new CustomEvent("h3-prompt-optimizer-settings-changed"));
}

const CATEGORY_ROOT = ["MiniMax H3 Context Loop", "Prompt optimizer"];

app.registerExtension({
    name: "minimax_h3_context_loop.prompt_optimizer_settings",
    init() {
        // Every setting needs its own distinct third category segment. The
        // settings panel's default (unsearched) view renders one row per
        // unique category path; six settings sharing the same literal leaf
        // ("Connection") collapsed onto a single visible row, so the other
        // five only ever turned up via search. Unique leaves fixed that.
        //
        // The panel also renders settings within a category in the REVERSE
        // of their addSetting() registration order (verified empirically
        // against this ComfyUI frontend build) rather than any documented
        // "order" field, so the add() calls below are deliberately listed
        // bottom-of-display-first to land in the intended top-to-bottom
        // order: Backend, API format, API URL, API key, MCP agent provider,
        // API model, Reference media. Re-check this ordering after a
        // ComfyUI frontend upgrade in case that rendering detail changes.
        const WIDE_FIELD = {style: "width: 100%; max-width: 480px;"};
        const add = (leaf, definition) => app.ui?.settings?.addSetting?.({
            category: [...CATEGORY_ROOT, leaf],
            ...definition,
            attrs: {...WIDE_FIELD, ...definition.attrs},
            onChange(value, previous) {
                definition.onChange?.(value, previous);
                notifyChanged();
            },
        });
        add("Reference media", {
            id: PROMPT_OPTIMIZER_SETTING_IDS.allowMedia,
            name: "Allow Direct API to read reference media",
            tooltip: "Off by default. Accessible reference files up to 32 MB are attached only when the selected API format supports their modality. OpenAI-compatible and Responses attach images; Gemini Native can attach image, video, and audio.",
            type: "boolean",
            defaultValue: false,
        });
        add("Direct API model", {
            id: PROMPT_OPTIMIZER_SETTING_IDS.model,
            name: "Direct API model",
            type: "text",
            defaultValue: "",
            attrs: {placeholder: "model identifier"},
        });
        add("MCP agent provider", {
            id: PROMPT_OPTIMIZER_SETTING_IDS.mcpProvider,
            name: "MCP agent provider",
            tooltip: "Used only when Prompt optimizer backend is MCP agent. The compatible comfyui-mcp bridge validates whether the provider is installed and authenticated.",
            type: "combo",
            defaultValue: "codex",
            options: ["codex", "claude", "gemini", "hermes", "kimi", "moonshot", "glm", "minimax", "ollama", "openrouter", "lmstudio", "llamacpp", "custom"],
            attrs: {editable: true, filter: true},
        });
        add("Direct API key", {
            id: PROMPT_OPTIMIZER_SETTING_IDS.apiKey,
            name: "Direct API key",
            tooltip: "Stored in ComfyUI user settings, never in workflow JSON. Local endpoints require an exact server-side origin allow-list entry; Gemini Native requires a key.",
            type: "text",
            defaultValue: "",
            attrs: {type: "password", autocomplete: "off"},
            telemetry: {trackChanges: false},
        });
        add("Direct API URL", {
            id: PROMPT_OPTIMIZER_SETTING_IDS.apiUrl,
            name: "Direct API URL",
            tooltip: "A provider base URL or complete supported endpoint. OpenAI, Gemini, and OpenRouter are allowed by default. Server operators can add exact origins through H3_PROMPT_OPTIMIZER_ALLOWED_ORIGINS.",
            type: "text",
            defaultValue: "",
            attrs: {placeholder: "https://api.example.com/v1"},
        });
        add("Direct API format", {
            id: PROMPT_OPTIMIZER_SETTING_IDS.apiFormat,
            name: "Direct API format",
            tooltip: "Which endpoint and request shape the Direct API call uses:\n\n" +
                "• OpenAI-compatible Chat Completions — POST {Direct API URL}/v1/chat/completions. " +
                "Works with OpenAI, most local servers (LM Studio, llama.cpp, Ollama's OpenAI-compatible " +
                "route, etc.), and most third-party proxies. Reference media: images only.\n\n" +
                "• OpenAI Responses — POST {Direct API URL}/v1/responses. OpenAI's newer Responses " +
                "API shape; use this only against a server that actually implements /v1/responses. " +
                "Reference media: images only.\n\n" +
                "• Gemini Native — POST {Direct API URL}/v1beta/models/{Direct API model}:" +
                "generateContent. Google's native Gemini request/response shape; requires a Direct API " +
                "key. Reference media: images, video, and audio.",
            type: "combo",
            defaultValue: "openai",
            options: [
                {text: "OpenAI-compatible Chat Completions", value: "openai"},
                {text: "OpenAI Responses", value: "responses"},
                {text: "Gemini Native", value: "gemini"},
            ],
        });
        add("Backend", {
            id: PROMPT_OPTIMIZER_SETTING_IDS.backend,
            name: "Prompt optimizer backend",
            tooltip: "Direct API works without comfyui-mcp and is the portable default. MCP agent uses the separately installed compatible orchestrator. Disabled hides optimizer execution.",
            type: "combo",
            defaultValue: "direct",
            options: [
                {text: "Direct API (default)", value: "direct"},
                {text: "MCP agent", value: "mcp"},
                {text: "Disabled", value: "disabled"},
            ],
        });
    },
});
