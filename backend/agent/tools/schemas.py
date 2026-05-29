from pydantic import BaseModel, Field


class WebSearchInput(BaseModel):
    """Input schema for the web search tool."""

    query: str = Field(min_length=1, description="Search query to send to Tavily.")


class KnowledgeSearchInput(BaseModel):
    """Input schema for local docx knowledge-base search."""

    query: str = Field(min_length=1, description="Question or keywords to search in uploaded DOCX text, tables, image OCR, and image descriptions.")
    top_k: int = Field(default=5, ge=1, le=10, description="Maximum document chunks to return.")


class WeatherInput(BaseModel):
    """Input schema for the weather tool."""

    location: str = Field(min_length=1, description="City or location name.")


class CurrentTimeInput(BaseModel):
    """Input schema for the current time tool."""

    timezone: str = Field(default="Asia/Shanghai", description="IANA timezone name, such as Asia/Shanghai or UTC.")


class CalculatorInput(BaseModel):
    """Input schema for the calculator tool."""

    expression: str = Field(min_length=1, description="Arithmetic expression using numbers and + - * / ** % ().")


class TelemetryLatestInput(BaseModel):
    """Input schema for latest telemetry lookup."""

    device_id: str | None = Field(default=None, description="Optional device ID to filter.")
    point_code: str | None = Field(default=None, description="Optional point code to filter.")
    limit: int = Field(default=10, ge=1, le=50, description="Maximum records to return.")


class TelemetrySummaryInput(BaseModel):
    """Input schema for telemetry aggregate analysis."""

    device_id: str = Field(min_length=1, description="Device ID to analyze.")
    point_code: str = Field(min_length=1, description="Telemetry point code to analyze.")
    start_time: str | None = Field(default=None, description="Optional inclusive start time, ISO format.")
    end_time: str | None = Field(default=None, description="Optional inclusive end time, ISO format.")


class TelemetryDevicesInput(BaseModel):
    """Input schema for listing telemetry devices."""

    limit: int = Field(default=20, ge=1, le=100, description="Maximum devices to return.")
