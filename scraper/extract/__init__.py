from scraper.extract.aggregator import aggregate
from scraper.extract.news_extractor import news_items_from_pages, parse_feed
from scraper.extract.page_processor import ProcessedPage, process_page

__all__ = ["ProcessedPage", "process_page", "parse_feed", "news_items_from_pages", "aggregate"]
