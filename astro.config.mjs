import { defineConfig } from "astro/config";
import starlight from "@astrojs/starlight";
import mermaid from "astro-mermaid";

export default defineConfig({
  site: "https://iha2.github.io",
  base: "/superlite",
  integrations: [
    mermaid({ theme: "default", autoTheme: true }),
    starlight({
      title: "superlite",
      logo: { src: "./src/assets/logo.svg" },
      social: [
        { icon: "github", label: "GitHub", href: "https://github.com/iha2/superlite" },
      ],
      editLink: { baseUrl: "https://github.com/iha2/superlite/edit/site/" },
      lastUpdated: true,
      customCss: ["./src/styles/custom.css"],
      sidebar: [
        { label: "Getting started", items: [{ autogenerate: { directory: "start" } }] },
        { label: "Flows", items: [{ autogenerate: { directory: "flows" } }] },
        { label: "How a run works", items: [{ autogenerate: { directory: "run" } }] },
        { label: "Sessions and worktrees", items: [{ autogenerate: { directory: "sessions" } }] },
        { label: "Reference", items: [{ autogenerate: { directory: "reference" } }] },
        { label: "Results", items: [{ autogenerate: { directory: "results" } }] },
      ],
    }),
  ],
});
