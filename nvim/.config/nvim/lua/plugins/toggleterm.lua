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

    -- Per-user scratch dir, so two logged-in users don't fight over the same /tmp files
    local tmp = os.getenv("XDG_RUNTIME_DIR") or "/tmp"
    local run_out = tmp .. "/nvim_run_out"

    -- %s is substituted with the current file's absolute path
    local run_commands = {
      python = "python3 %s",
      lua = "lua %s",
      sh = "bash %s",
      bash = "bash %s",
      javascript = "node %s",
      typescript = "npx ts-node %s",
      c = "gcc %s -o " .. run_out .. " && " .. run_out,
      cpp = "g++ %s -o " .. run_out .. " && " .. run_out,
      rust = "rustc %s -o " .. run_out .. " && " .. run_out,
      go = "go run %s",
      r = "Rscript %s",
    }

    local run_socket = "unix:" .. tmp .. "/kitty-nvim-run.sock"

    local function run_window_alive()
      vim.fn.system({ "kitty", "@", "--to", run_socket, "ls" })
      return vim.v.shell_error == 0
    end

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

      if run_window_alive() then
        -- interrupt whatever's running, clear the scrollback, then run the new command
        vim.fn.system({ "kitty", "@", "--to", run_socket, "send-text", "\x03" })
        vim.fn.system({ "kitty", "@", "--to", run_socket, "send-text", "clear\n" })
        vim.fn.system({ "kitty", "@", "--to", run_socket, "send-text", cmd .. "\n" })
      else
        vim.fn.jobstart({
          "kitty",
          "--detach",
          "--listen-on",
          run_socket,
          "-o",
          "allow_remote_control=yes",
          "-e",
          "bash",
          "-c",
          cmd .. "; exec bash",
        }, { detach = true })
      end
    end

    vim.keymap.set("n", "<F5>", run_current_file, { desc = "Run current file in a detached kitty window" })
  end,
}
