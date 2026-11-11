# -*- coding: utf-8 -*-
"""Интерактивные элементы: подтверждение опасного действия и листалка истории."""

from __future__ import annotations

import discord

import formatting as fmt


class ConfirmView(discord.ui.View):
    """Да / нет. Используется для снятия администратора."""

    def __init__(self, author_id: int):
        super().__init__(timeout=60)
        self.author_id = author_id
        self.result: bool | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Эта кнопка не для вас.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Подтвердить", style=discord.ButtonStyle.danger)
    async def yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.result = True
        await interaction.response.edit_message(content="Подтверждено.", view=None)
        self.stop()

    @discord.ui.button(label="Отмена", style=discord.ButtonStyle.secondary)
    async def no(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.result = False
        await interaction.response.edit_message(content="Отменено.", view=None)
        self.stop()

    @classmethod
    async def ask(cls, interaction: discord.Interaction, text: str) -> bool:
        view = cls(interaction.user.id)
        await interaction.response.send_message(text, view=view, ephemeral=True)
        await view.wait()
        try:
            await interaction.delete_original_response()
        except Exception:
            pass
        return bool(view.result)


class LogsView(discord.ui.View):
    """История действий администратора с кнопками ◀ ▶."""

    PER_PAGE = 8

    def __init__(
        self,
        target,
        member_row,
        role_text: str | None,
        actions: list[dict],
        page: int = 1,
    ):
        super().__init__(timeout=300)
        self.target = target
        self.member_row = member_row
        self.role_text = role_text
        self.actions = actions
        self.page = page

    @property
    def total_pages(self) -> int:
        return max(1, (len(self.actions) + self.PER_PAGE - 1) // self.PER_PAGE)

    def embed(self) -> discord.Embed:
        return fmt.logs_embed(
            self.target,
            self.member_row,
            self.role_text,
            self.actions,
            page=self.page,
            per_page=self.PER_PAGE,
        )

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(1, self.page - 1)
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = min(self.total_pages, self.page + 1)
        await interaction.response.edit_message(embed=self.embed(), view=self)
