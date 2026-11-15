# Skills for Claude and OpenCode

These skills teach coding agents how the Arduino UNO Q, this repository, and the Math on the Edge datathon work. With them, the agent gives you commands and code that run on the board, inside its limits.

| Skill | Use it to |
| --- | --- |
| [`unoq-board`](unoq-board/SKILL.md) | Connect to the board, check the model services and the RAM, and fix common problems |
| [`m3kang-copilot`](m3kang-copilot/SKILL.md) | Change the copilot, run measured experiments, and analyse the errors |
| [`unoq-hardware-ui`](unoq-hardware-ui/SKILL.md) | Build an App Lab app with a web page and the LED matrix, connected to the copilot |
| [`publish-project`](publish-project/SKILL.md) | Prepare your public GitHub repository and your Arduino Project Hub write-up |
| [`minddragon`](minddragon/SKILL.md) | Operate and demo the copilot: ports, the scored entry point, how to read the results honestly, and the failure modes that are not bugs |

Each skill is a folder with a `SKILL.md` file: a short description that tells the agent when to use it, then the instructions.

## Use them with OpenCode

OpenCode discovers `.opencode/skills/` in the repository automatically: start `opencode` in the repository and the skills are advertised by their descriptions. You can also ask for one by name, such as `@m3kang-copilot`.

## Use them with Claude Code

On your laptop, from your clone of the repository:

```bash
mkdir -p .claude
ln -s ../.opencode/skills .claude/skills
```

Start `claude` in the same folder. It loads a skill when your request fits its description, or you can ask for one by name:

- "Use the unoq-board skill: the language model service doesn't answer."
- "Use the m3kang-copilot skill to add a hint ladder, and measure it."
- "Use the unoq-hardware-ui skill: show the student's answer on the LED matrix, with a tick or a cross."

To use them in every project on your laptop, copy the folders to `~/.claude/skills/` instead.

## Use them in the Claude app

Zip one skill folder, such as `m3kang-copilot`, and upload it in the Claude app's settings, where you manage skills. Or paste the `SKILL.md` text at the start of a conversation.

## Keep them up to date

If you change how your copilot works, such as the ports, the models, or the rules your code keeps, change the skills too. They're part of your repository, and Claude follows what they say.
