return {
  "akinsho/toggleterm.nvim",
  version = "*",
  event = "VeryLazy",
  config = function()
    require("toggleterm").setup({
      open_mapping = [[<C-\>]],
      direction = "float",
      float_opts = { border = "curved" },
      size = 15,
    })

    local Terminal = require("toggleterm.terminal").Terminal

    -- %s is substituted with the current file's absolute path
    local run_commands = {
      python = "python3 %s",
      lua = "lua %s",
      sh = "bash %s",
      bash = "bash %s",
      javascript = "node %s",
      typescript = "npx ts-node %s",
      c = "gcc %s -o /tmp/nvim_run_out && /tmp/nvim_run_out",
      cpp = "g++ %s -o /tmp/nvim_run_out && /tmp/nvim_run_out",
      rust = "rustc %s -o /tmp/nvim_run_out && /tmp/nvim_run_out",
      go = "go run %s",
      r = "Rscript %s",
    }

    local function run_current_file()
      local filetype = vim.bo.filetype
      local template = run_commands[filetype]

      if not template then
        vim.notify("No run command configured for filetype: " .. filetype, vim.log.levels.WARN)
        return
      end

      vim.cmd("write")

      local file = vim.fn.shellescape(vim.fn.expand("%:p"))
      local cmd = string.format(template, file)
      local run_term = Terminal:new({ cmd = cmd, direction = "float", close_on_exit = false })
      run_term:toggle()
    end

    vim.keymap.set("n", "<F5>", run_current_file, { desc = "Run current file in terminal" })
  end,
}
