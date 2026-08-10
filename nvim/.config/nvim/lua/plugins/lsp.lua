return {
  {
    "williamboman/mason.nvim",
    config = true,
  },
  {
    "williamboman/mason-lspconfig.nvim",
    dependencies = { "williamboman/mason.nvim" },
    opts = {
      -- r_language_server intentionally excluded: mason's build of it fails
      -- in this environment (a fork/cwd bug during R's parallel package
      -- compile), so the CRAN `languageserver` package is installed
      -- directly instead and used via nvim-lspconfig's default `R` command.
      ensure_installed = { "clangd", "pyright", "bashls" },
    },
  },
  {
    "neovim/nvim-lspconfig",
    dependencies = { "williamboman/mason-lspconfig.nvim", "saghen/blink.cmp" },
    config = function()
      local capabilities = require("blink.cmp").get_lsp_capabilities()

      local servers = { "clangd", "pyright", "bashls", "r_language_server" }
      for _, server in ipairs(servers) do
        vim.lsp.config(server, { capabilities = capabilities })
        vim.lsp.enable(server)
      end
    end,
  },
}
