import asyncio

from reminder_agent.scheduler.gateway import ConsoleGateway


def test_console_gateway_prints_reminder(capsys):
    asyncio.run(
        ConsoleGateway().send(
            delivery_id=11,
            user_id=7,
            text="Submit claim",
        )
    )

    assert capsys.readouterr().out == "REMINDER: Submit claim\n"
