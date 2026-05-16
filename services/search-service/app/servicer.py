import grpc


class SearchServicer:
    """Implements search.proto SearchService."""

    async def CreateSearch(self, request, context):
        # 1. Insert LandSearch row (status=pending)
        # 2. Insert SearchCriteria stub (confirmed=False) - criteria filled by SearchCriteriaAgent
        # 3. Publish job to Redis → ai-orchestrator Celery (run_search_task)
        # 4. Return search_id
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)

    async def GetSearch(self, request, context):
        # Query LandSearch by search_id, return SearchResponse
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)

    async def ListSearches(self, request, context):
        # Query all LandSearches for user_id with pagination
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)

    async def GetSearchStatus(self, request, context):
        # Query search_steps for latest agent_name + progress_pct
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)

    async def GetSearchCriteria(self, request, context):
        # Return SearchCriteria.criteria_json + confirmed flag
        # Frontend polls until confirmed=True to show results
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)

    async def ConfirmCriteria(self, request, context):
        # User accepted/edited criteria → set confirmed=True, resume pipeline
        # ai-orchestrator polls for this before running LandScout
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)

    async def GetSearchResults(self, request, context):
        # Query search_candidates ordered by rank
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)

    async def GetRecommendation(self, request, context):
        # Query search_recommendations for final ranked result
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)

    async def SaveCriteria(self, request, context):
        # Called by ai-orchestrator after SearchCriteriaAgent completes
        # Upsert SearchCriteria with structured criteria_json
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)

    async def SaveCandidate(self, request, context):
        # Called by ai-orchestrator as each candidate is scored
        # Insert/update SearchCandidate with rank + scores_json
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)

    async def UpdateSearchProgress(self, request, context):
        # Called by ai-orchestrator after each agent step
        # Upsert SearchStep with latest status + progress_pct
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
