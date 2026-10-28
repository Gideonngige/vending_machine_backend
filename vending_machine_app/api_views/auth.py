
from .common_imports import *


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def register(request):

    serializer = RegisterSerializer(
        data=request.data
    )

    if not serializer.is_valid():
        return Response(
            serializer.errors,
            status=status.HTTP_400_BAD_REQUEST,
        )

    user = serializer.save()

    business = serializer.created_business

    return Response(
        {
            "success": True,
            "message": (
                "Your account and business "
                "have been registered successfully."
            ),
            "user": UserSerializer(user).data,
            "business": {
                "id": business.id,
                "name": business.name,
                "slug": business.slug,
                "phone": business.phone,
                "email": business.email,
                "county": business.county,
                "address": business.address,
                "status": business.status,
            },
        },
        status=status.HTTP_201_CREATED,
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def my_profile(request):
    return Response(UserSerializer(request.user).data)
