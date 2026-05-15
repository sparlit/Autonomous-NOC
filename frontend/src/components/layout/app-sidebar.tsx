
import * as React from "react"
import {
  LayoutDashboard,
  Activity,
  AlertTriangle,
  Terminal,
  Settings,
  Shield,
  Network,
  Database,
} from "lucide-react"

import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarMenuSub,
  SidebarMenuSubButton,
  SidebarMenuSubItem,
  SidebarGroup,
  SidebarGroupLabel,
} from "@/components/ui/sidebar"

const data = {
  navMain: [
    {
      title: "Dashboard",
      url: "/",
      icon: LayoutDashboard,
      isActive: true,
    },
    {
      title: "Monitoring",
      url: "#",
      icon: Activity,
      items: [
        {
          title: "Network Metrics",
          url: "/",
        },
        {
          title: "Agent Operations",
          url: "/agents",
        },
      ],
    },
    {
      title: "Alerts",
      url: "#",
      icon: AlertTriangle,
      items: [
        {
          title: "Active Incidents",
          url: "#",
        },
        {
          title: "Alert History",
          url: "#",
        },
      ],
    },
    {
      title: "Network",
      url: "#",
      icon: Network,
      items: [
        {
          title: "Topology Map",
          url: "/topology",
        },
        {
          title: "Device Inventory",
          url: "#",
        },
      ],
    },
    {
      title: "Automation",
      url: "#",
      icon: Database,
      items: [
        {
          title: "Task Board",
          url: "/tasks",
        },
        {
          title: "Action Logs",
          url: "#",
        },
      ],
    },
    {
      title: "Terminal",
      url: "/terminal",
      icon: Terminal,
    },
  ],
  secondary: [
    {
      title: "Settings",
      url: "#",
      icon: Settings,
    },
    {
      title: "Security",
      url: "#",
      icon: Shield,
    },
  ],
}

export function AppSidebar({ ...props }: React.ComponentProps<typeof Sidebar>) {
  return (
    <Sidebar variant="inset" {...props}>
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton size="lg">
              <div className="flex aspect-square size-8 items-center justify-center rounded-lg bg-sidebar-primary text-sidebar-primary-foreground">
                <Shield className="size-4" />
              </div>
              <div className="grid flex-1 text-left text-sm leading-tight">
                <span className="truncate font-semibold">Autonomous NOC</span>
                <span className="truncate text-xs">System Administrator</span>
              </div>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupLabel>Platform</SidebarGroupLabel>
          <SidebarMenu>
            {data.navMain.map((item) => (
              <SidebarMenuItem key={item.title}>
                <SidebarMenuButton asChild tooltip={item.title} isActive={item.isActive}>
                  <a href={item.url}>
                    {item.icon && <item.icon />}
                    <span>{item.title}</span>
                  </a>
                </SidebarMenuButton>
                {item.items?.length ? (
                  <SidebarMenuSub>
                    {item.items.map((subItem) => (
                      <SidebarMenuSubItem key={subItem.title}>
                        <SidebarMenuSubButton href={subItem.url}>
                          {subItem.title}
                        </SidebarMenuSubButton>
                      </SidebarMenuSubItem>
                    ))}
                  </SidebarMenuSub>
                ) : null}
              </SidebarMenuItem>
            ))}
          </SidebarMenu>
        </SidebarGroup>
      </SidebarContent>
      <SidebarFooter>
        <SidebarMenu>
          {data.secondary.map((item) => (
            <SidebarMenuItem key={item.title}>
              <SidebarMenuButton asChild size="sm">
                <a href={item.url}>
                  <item.icon />
                  <span>{item.title}</span>
                </a>
              </SidebarMenuButton>
            </SidebarMenuItem>
          ))}
        </SidebarMenu>
      </SidebarFooter>
    </Sidebar>
  )
}
