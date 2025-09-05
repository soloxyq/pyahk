╭──────────────────────────────────────────────────────────────────────────────────────────────────────────────╮
│                                                                                                              │
│ Basics:                                                                                                      │
│ Add context: Use @ to specify files for context (e.g., @src/myFile.ts) to target specific files or folders.  │
│ Shell mode: Execute shell commands via ! (e.g., !npm run start) or use natural language (e.g. start server). │
│                                                                                                              │
│ Commands:                                                                                                    │
│  /about - show version info                                                                                  │
│  /auth - change the auth method                                                                              │
│  /bug - submit a bug report                                                                                  │
│  /chat - Manage conversation history.                                                                        │
│    list - List saved conversation checkpoints                                                                │
│    save - Save the current conversation as a checkpoint. Usage: /chat save <tag>                             │
│    resume - Resume a conversation from a checkpoint. Usage: /chat resume <tag>                               │
│    delete - Delete a conversation checkpoint. Usage: /chat delete <tag>                                      │
│  /clear - clear the screen and conversation history                                                          │
│  /compress - Compresses the context by replacing it with a summary.                                          │
│  /copy - Copy the last result or code snippet to clipboard                                                   │
│  /corgi - Toggles corgi mode.                                                                                │
│  /docs - open full Gemini CLI documentation in your browser                                                  │
│  /directory - Manage workspace directories                                                                   │
│    add - Add directories to the workspace. Use comma to separate multiple paths                              │
│    show - Show all directories in the workspace                                                              │
│  /editor - set external editor preference                                                                    │
│  /extensions - list active extensions                                                                        │
│  /help - for help on gemini-cli                                                                              │
│  /init - Analyzes the project and creates a tailored GEMINI.md file.                                         │
│  /mcp - list configured MCP servers and tools, or authenticate with OAuth-enabled servers                    │
│    list - List configured MCP servers and tools                                                              │
│    auth - Authenticate with an OAuth-enabled MCP server                                                      │
│    refresh - Refresh the list of MCP servers and tools                                                       │
│  /memory - Commands for interacting with memory.                                                             │
│    show - Show the current memory contents.                                                                  │
│    add - Add content to the memory.                                                                          │
│    refresh - Refresh the memory from the source.                                                             │
│  /privacy - display the privacy notice                                                                       │
│  /quit - exit the cli                                                                                        │
│  /stats - check session stats. Usage: /stats [model|tools]                                                   │
│    model - Show model-specific usage statistics.                                                             │
│    tools - Show tool-specific usage statistics.                                                              │
│  /theme - change the theme                                                                                   │
│  /tools - list available Gemini CLI tools                                                                    │
│  /vim - toggle vim mode on/off                                                                               │
│  ! - shell command                                                                                           │
│                                                                                                              │
│ Keyboard Shortcuts:                                                                                          │
│ Alt+Left/Right - Jump through words in the input                                                             │
│ Ctrl+C - Quit application                                                                                    │
│ Ctrl+Enter - New line                                                                                        │
│ Ctrl+L - Clear the screen                                                                                    │
│ Ctrl+X - Open input in external editor                                                                       │
│ Ctrl+Y - Toggle YOLO mode                                                                                    │
│ Enter - Send message                                                                                         │
│ Esc - Cancel operation                                                                                       │
│ Shift+Tab - Toggle auto-accepting edits                                                                      │
│ Up/Down - Cycle through your prompt history                                                                  │
│                                                                                                              │
│ For a full list of shortcuts, see docs/keyboard-shortcuts.md                                                 │
│                                                                                                              │
╰──────────────────────────────────────────────────────────────────────────────────────────────────────────────╯