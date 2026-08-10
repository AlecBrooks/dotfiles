return {
  "ellisonleao/gruvbox.nvim",
  priority = 1000,
  lazy = false,
  config = function()
    require("gruvbox").setup({
      terminal_colors = true,
      overrides = {
        -- Muted teal / sage accents, matching the hyprland active window
        -- border and the kitty gruvbox-teal theme
        MatchParen   = { fg = "#6ba8c0", bold = true },
        CursorLineNr = { fg = "#6ba8c0", bold = true },
        Title        = { fg = "#6ba8c0", bold = true },
        Directory    = { fg = "#6ba8c0" },
        Search       = { bg = "#46b98b", fg = "#282828" },
        IncSearch    = { bg = "#6ba8c0", fg = "#282828" },
      },
    })
    vim.cmd.colorscheme("gruvbox")
  end,
}
