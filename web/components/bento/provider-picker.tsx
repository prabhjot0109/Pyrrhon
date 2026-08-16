import type React from "react"
import { Search } from "lucide-react"

interface ProviderPickerProps {
  className?: string
}

/**
 * The provider list Pyrrhon actually ships, mirroring BUILTIN_PROVIDERS in
 * pyrrhon/config/settings.py. `local` marks the two that need no API key and
 * no network: the model runs on the same machine as the agent.
 */
const ProviderPicker: React.FC<ProviderPickerProps> = ({ className = "" }) => {
  const providers = [
    { name: "groq", local: false },
    { name: "openai", local: false },
    { name: "cerebras", local: false },
    { name: "gemini", local: false },
    { name: "ollama", local: true },
    { name: "lmstudio", local: true },
  ]

  const mono = "'Geist Mono', ui-monospace, SFMono-Regular, Menlo, monospace"

  return (
    <div
      className={`w-full h-full flex items-center justify-center p-4 relative ${className}`}
      role="img"
      aria-label="Provider picker listing the model providers Pyrrhon supports"
    >
      <div
        style={{
          position: "absolute",
          top: "50%",
          left: "50%",
          transform: "translate(-50%, calc(-50% + 24px))",
          width: "345px",
          height: "277px",
          background: "linear-gradient(180deg, hsl(var(--background)) 0%, transparent 100%)",
          backdropFilter: "blur(16px)",
          borderRadius: "9.628px",
          border: "0.802px solid hsl(var(--border))",
          overflow: "hidden",
        }}
      >
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            height: "100%",
            width: "100%",
          }}
        >
          {/* Search Header */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: "12.837px",
              padding: "8.826px 12.837px",
              borderBottom: "0.802px solid hsl(var(--border))",
              width: "100%",
              boxSizing: "border-box",
            }}
          >
            <div
              style={{
                width: "14.442px",
                height: "14.442px",
                position: "relative",
                flexShrink: 0,
              }}
            >
              <Search className="w-full h-full text-muted-foreground" />
            </div>
            <span
              style={{
                fontFamily: mono,
                fontSize: "12.837px",
                lineHeight: "19.256px",
                color: "hsl(var(--muted-foreground))",
                fontWeight: 400,
                whiteSpace: "nowrap",
              }}
            >
              /settings &mdash; choose a provider
            </span>
          </div>
          {/* Provider List */}
          {providers.map((provider, index) => (
            <div
              key={provider.name}
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                padding: "8.826px 12.837px",
                borderBottom: index < providers.length - 1 ? "0.479px solid hsl(var(--border))" : "none",
                width: "100%",
                boxSizing: "border-box",
              }}
            >
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "12.837px",
                }}
              >
                <div
                  style={{
                    width: "6px",
                    height: "6px",
                    borderRadius: "50%",
                    flexShrink: 0,
                    background: provider.local
                      ? "hsl(var(--primary))"
                      : "hsl(var(--muted-foreground) / 0.4)",
                  }}
                />
                <span
                  style={{
                    fontFamily: mono,
                    fontSize: "12.837px",
                    lineHeight: "19.256px",
                    color: "hsl(var(--muted-foreground))",
                    fontWeight: 400,
                    whiteSpace: "nowrap",
                  }}
                >
                  {provider.name}
                </span>
              </div>
              {provider.local && (
                <div
                  style={{
                    background: "hsl(var(--primary) / 0.08)",
                    padding: "1.318px 5.272px",
                    borderRadius: "3.295px",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                  }}
                >
                  <span
                    style={{
                      fontFamily: mono,
                      fontSize: "9.583px",
                      lineHeight: "15.333px",
                      color: "hsl(var(--primary))",
                      fontWeight: 500,
                      whiteSpace: "nowrap",
                    }}
                  >
                    local
                  </span>
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

export default ProviderPicker
