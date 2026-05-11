from tools.calc_tool import calculator
from tools.compare_tool import compare_packages
from tools.product_tool import query_products
from tools.recommend_tool import recommend_packages
from tools.requirement_parser import parse_requirements
from tools.search_tool import search_internet

ALL_TOOLS = [
    calculator,
    compare_packages,
    parse_requirements,
    query_products,
    recommend_packages,
    search_internet
]
