
from .common_imports import *





def permitted_machines(user):
    return Machine.objects.filter(
        business__in=accessible_businesses(user)
    ).select_related("business")


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def machine_list_create(request):
    if request.method == "GET":
        machines = permitted_machines(request.user)

        business_id = request.query_params.get("business")
        if business_id:
            machines = machines.filter(business_id=business_id)

        serializer = MachineSerializer(
            machines.order_by("-created_at"),
            many=True,
        )
        return Response(serializer.data)

    serializer = MachineSerializer(
        data=request.data,
        context={"request": request},
    )
    serializer.is_valid(raise_exception=True)
    machine = serializer.save()

    return Response(
        {
            "message": "Machine registered successfully.",
            "machine": MachineSerializer(machine).data,
        },
        status=status.HTTP_201_CREATED,
    )


@api_view(["GET", "PATCH", "DELETE"])
@permission_classes([IsAuthenticated])
def machine_detail(request, machine_id):
    machine = get_object_or_404(
        permitted_machines(request.user),
        pk=machine_id,
    )

    if request.method == "GET":
        return Response(MachineSerializer(machine).data)

    require_business_role(request.user, machine.business)

    if request.method == "DELETE":
        machine.is_active = False
        machine.accepts_online_orders = False
        machine.status = "OFFLINE"
        machine.save(update_fields=[
            "is_active",
            "accepts_online_orders",
            "status",
            "updated_at",
        ])
        return Response({
            "message": "Machine deactivated successfully."
        })

    serializer = MachineSerializer(
        machine,
        data=request.data,
        partial=True,
        context={"request": request},
    )
    serializer.is_valid(raise_exception=True)
    serializer.save()

    return Response({
        "message": "Machine updated successfully.",
        "machine": serializer.data,
    })
