"""Product document extraction and business schema tools."""

__all__ = ["build_document", "transform_document"]


def __getattr__(name):
    if name == "build_document":
        from agent.product_doc_agent.extract_docs import build_document

        return build_document
    if name == "transform_document":
        from agent.product_doc_agent.build_business_schema import transform_document

        return transform_document
    raise AttributeError(name)
