from langchain_core.tools import tool

from agent.tools.schemas import WeatherInput


@tool(args_schema=WeatherInput)
def get_weather(location: str) -> str:
    """
    Get the weather in a given location.

    :return: str
    """
    # Placeholder implementation. Replace this with a real weather API adapter later.
    return f"Current weather in {location} is sunny."
